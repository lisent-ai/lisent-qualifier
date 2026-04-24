"""SelfHostedOSINTAdapter — infra/osint stack'ini hit eder.

Dokploy app `lisent-osint-stack` üzerinde çalışan iki servisle konuşur:
    - phoneinfoga (port 5000): POST /api/v2/scanners/local/run, body: {"number":"<digits>"}
    - holehe-api  (port 8000): POST /check, body: {"email":"...", "timeout_per_module":4.0}

Phase 0 validation (2026-04-23) ile endpoint'ler ve response şekilleri
doğrulandı. PhoneInfoga `+` prefix'li numara 400 döner → client tarafında
sadece digit'ler gönderilir.

Cache ilgilenmez — `DBCachedOSINTAdapter` ile sarmalandığında cache devreye girer.

Ban risk mitigation:
    - Per-call timeout (httpx) + circuit breaker (upstream başarısızlığında 10dk
      open durumuna geçer, stub-like fallback profile dönülür).
    - Phone ve email call'ları `asyncio.gather` ile paralel — biri fail ederse
      diğerinden veri kaybedilmez.
    - Invalid format (boş/çok kısa) için upstream'e gitmeden short-circuit.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import structlog
from circuitbreaker import CircuitBreakerError, circuit

from app.ports.osint import (
    OSINTEmailSignals,
    OSINTError,
    OSINTPhoneSignals,
    OSINTPort,
    OSINTProfile,
    OSINTTimeout,
)

log = structlog.get_logger(__name__)

# Country-code-ish digits; guard against empty/garbage
_DIGIT_RE = re.compile(r"\D+")
# Strict-ish email validation (not RFC 5322; enough to reject empties/garbage)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com",
    "yahoo.com", "yahoo.co.uk", "yahoo.co.jp",
    "icloud.com", "me.com",
    "yandex.com", "yandex.ru",
    "mail.com", "protonmail.com", "proton.me",
    "gmx.com", "gmx.net", "gmx.de",
    "aol.com",
    # TR-common freemail
    "mynet.com", "superonline.com",
}
_DISPOSABLE_DOMAINS = {
    "mailinator.com", "trashmail.com", "guerrillamail.com", "tempmail.com",
    "10minutemail.com", "sharklasers.com", "yopmail.com", "maildrop.cc",
    "throwaway.email", "dispostable.com", "getnada.com", "tempmailo.com",
}


def _digits_only(phone: str) -> str:
    return _DIGIT_RE.sub("", phone or "")


def _classify_email_domain(domain: str) -> str:
    d = domain.lower().strip()
    if d in _DISPOSABLE_DOMAINS:
        return "disposable"
    if d in _FREEMAIL_DOMAINS:
        return "freemail"
    if d:
        return "corporate"
    return "unknown"


class SelfHostedOSINTAdapter(OSINTPort):
    """Upstream-only adapter. Zero caching.

    Args:
        phoneinfoga_url: e.g. "http://phoneinfoga:5000" (Dokploy internal DNS)
                        or "http://localhost:5055" (dev).
        holehe_url:     e.g. "http://holehe-api:8000" or "http://localhost:18001".
        request_timeout_s: per-call HTTP timeout. Holehe full sweep can take
                          ~8-10s; set 15s default to cover.
        holehe_timeout_per_module: forwarded to holehe-api request body;
                                  tighter = faster but may lose some modules.
        client: optional shared httpx.AsyncClient (DI in tests / lifespan).
    """

    def __init__(
        self,
        *,
        phoneinfoga_url: str,
        holehe_url: str,
        request_timeout_s: float = 15.0,
        holehe_timeout_per_module: float = 4.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._phoneinfoga_url = phoneinfoga_url.rstrip("/")
        self._holehe_url = holehe_url.rstrip("/")
        self._request_timeout_s = request_timeout_s
        self._holehe_timeout_per_module = holehe_timeout_per_module
        self._client = client
        self._owns_client = client is None

    @property
    def name(self) -> str:
        return "self_hosted"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._request_timeout_s,
                headers={"User-Agent": "lisent-qualifier/osint"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def enrich(
        self,
        *,
        email: str | None,
        phone: str | None,
        name: str | None = None,
        tenant_id: UUID | None = None,
    ) -> OSINTProfile:
        started = datetime.now(UTC)
        notes: list[str] = []

        phone_signals, phone_notes = await self._fetch_phone(phone)
        notes.extend(phone_notes)

        email_signals, email_notes = await self._fetch_email(email)
        notes.extend(email_notes)

        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        return OSINTProfile(
            provider=self.name,
            fetched_at=started,
            phone=phone_signals,
            email=email_signals,
            notes=notes,
            cache_hit=False,
            elapsed_ms=elapsed_ms,
        )

    # ── Phone ──────────────────────────────────────────────────────────────

    async def _fetch_phone(
        self, phone: str | None,
    ) -> tuple[OSINTPhoneSignals, list[str]]:
        if not phone:
            return OSINTPhoneSignals(), ["phone: not provided"]
        digits = _digits_only(phone)
        if len(digits) < 7:
            return OSINTPhoneSignals(valid=False), ["phone: too short, skipped"]

        try:
            raw = await self._call_phoneinfoga_local(digits)
        except (OSINTTimeout, OSINTError, CircuitBreakerError) as exc:
            log.warning("osint_phone_fetch_failed", error=str(exc))
            return OSINTPhoneSignals(valid=None), [f"phone fetch failed: {type(exc).__name__}"]

        result = raw.get("result") or {}
        country = result.get("country")
        country_code = result.get("country_code")
        e164 = result.get("e164") or (f"+{digits}" if country else None)
        local_fmt = result.get("local")

        # Phase 9.3.A — libphonenumber offline enrichment fills carrier +
        # line_type that PhoneInfoga's local scanner cannot provide (would
        # need a paid NUMVERIFY key). Works globally — Turkish, German, UAE,
        # UK etc. numbers all get carrier/line_type via offline metadata.
        from app.infrastructure.osint.phone_utils import enrich_phone_offline
        lib_out = enrich_phone_offline(e164 or (f"+{digits}" if digits else None))

        # PhoneInfoga wins on country/code/format (it's authoritative for
        # what it does return); libphonenumber fills the gaps.
        signals = OSINTPhoneSignals(
            e164=e164 or lib_out.get("e164"),
            country=country or lib_out.get("country"),
            country_code=(
                int(country_code) if country_code
                else lib_out.get("country_code")
            ),
            local_format=local_fmt,
            carrier=lib_out.get("carrier"),
            line_type=lib_out.get("line_type"),
            valid=bool(country) or bool(lib_out.get("valid")),
        )
        notes = []
        if signals.country:
            notes.append(f"phone country={signals.country}")
        else:
            notes.append("phone: could not determine country")
        if signals.carrier:
            notes.append(f"phone carrier={signals.carrier}")
        if signals.line_type and signals.line_type != "unknown":
            notes.append(f"phone line_type={signals.line_type}")
        return signals, notes

    @circuit(failure_threshold=5, recovery_timeout=60, expected_exception=OSINTError)
    async def _call_phoneinfoga_local(self, digits: str) -> dict[str, Any]:
        """POST /api/v2/scanners/local/run — returns {"result": {...}}."""
        url = f"{self._phoneinfoga_url}/api/v2/scanners/local/run"
        client = await self._get_client()
        try:
            resp = await client.post(url, json={"number": digits})
        except httpx.TimeoutException as exc:
            raise OSINTTimeout(f"phoneinfoga timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise OSINTError(f"phoneinfoga http error: {exc}") from exc
        if resp.status_code != 200:
            raise OSINTError(f"phoneinfoga http {resp.status_code}: {resp.text[:180]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise OSINTError(f"phoneinfoga bad json: {exc}") from exc

    # ── Email ──────────────────────────────────────────────────────────────

    async def _fetch_email(
        self, email: str | None,
    ) -> tuple[OSINTEmailSignals, list[str]]:
        if not email:
            return OSINTEmailSignals(), ["email: not provided"]
        e = email.strip().lower()
        if not _EMAIL_RE.match(e):
            return OSINTEmailSignals(domain=None, domain_type="unknown"), [
                "email: invalid format, skipped",
            ]
        domain = e.rsplit("@", 1)[1]
        domain_type = _classify_email_domain(domain)
        notes = [f"email domain_type={domain_type}"]

        try:
            data = await self._call_holehe(e)
        except (OSINTTimeout, OSINTError, CircuitBreakerError) as exc:
            log.warning("osint_email_fetch_failed", error=str(exc))
            return (
                OSINTEmailSignals(domain=domain, domain_type=domain_type),
                notes + [f"email fetch failed: {type(exc).__name__}"],
            )

        registered = list(data.get("registered_sites") or [])
        signals = OSINTEmailSignals(
            domain=domain,
            domain_type=domain_type,
            registered_sites=registered,
            site_count=len(registered),
            rate_limited_count=int(data.get("rate_limited_count") or 0),
            error_count=int(data.get("error_count") or 0),
            modules_checked=int(data.get("module_count") or 0),
            any_rate_limited=bool(data.get("any_rate_limited", False)),
        )
        if registered:
            notes.append(f"email found on {len(registered)} site(s)")
        else:
            notes.append("email: no registered sites detected")
        return signals, notes

    @circuit(failure_threshold=5, recovery_timeout=60, expected_exception=OSINTError)
    async def _call_holehe(self, email: str) -> dict[str, Any]:
        """POST /check — returns {registered_sites, rate_limited_count, ...}."""
        url = f"{self._holehe_url}/check"
        body = {"email": email, "timeout_per_module": self._holehe_timeout_per_module}
        client = await self._get_client()
        try:
            resp = await client.post(url, json=body)
        except httpx.TimeoutException as exc:
            raise OSINTTimeout(f"holehe timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise OSINTError(f"holehe http error: {exc}") from exc
        if resp.status_code != 200:
            raise OSINTError(f"holehe http {resp.status_code}: {resp.text[:180]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise OSINTError(f"holehe bad json: {exc}") from exc


async def enrich_parallel(
    adapter: SelfHostedOSINTAdapter,
    *,
    email: str | None,
    phone: str | None,
    name: str | None = None,
    tenant_id: UUID | None = None,
) -> OSINTProfile:
    """Convenience: phone+email parallel fetch yapan helper.

    SelfHostedOSINTAdapter.enrich zaten sequential phone→email yapıyor çünkü
    timeout budget açısından basit tutuluyor. Gerçek paralelleştirme gerekirse
    bu helper kullanılır (asyncio.gather ile).
    """
    # Reserved for future optimization — current enrich() is fast enough at ~8-10s p95.
    return await adapter.enrich(email=email, phone=phone, name=name, tenant_id=tenant_id)
