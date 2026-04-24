"""OSINTPort — "Bu lead hakkında açık kaynaklardan ne öğrenebiliyoruz?"

Pre-scoring pipeline'ı email + telefon üzerinden public OSINT zenginleştirme
yapar ve LLM judge'a besler. Port + adapter ayrımı:

    OSINTPort              — ABC (bu dosya)
    StubOSINTAdapter       — boş profile döner (tests, OSINT kapalıyken)
    SelfHostedOSINTAdapter — infra/osint stack'ini hit eder (phoneinfoga + holehe-api)
    DBCachedOSINTAdapter   — qualifier_osint_profiles tablosunu önüne koyar

Tenant-aware: `enrich()` tenant_id alır; DB cache tenant-scoped (RLS). Aynı email/
telefon farklı tenant'larda ayrı ayrı cache'lenir (veri tenant'a aittir).

Phase 0 validation notes (2026-04-23):
    - PhoneInfoga v2.11 POST /api/v2/scanners/local/run, body: {"number":"<digits>"}
    - Holehe-api POST /check, body: {"email":"..."}; 121 modül, rate_limited %50
      normal (niş siteler 429'la cevap verir — signal değil gürültü), registered_sites
      listesi asıl sinyal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class OSINTPhoneSignals:
    """Telefon OSINT sinyalleri (phoneinfoga local scanner + opsiyonel numverify)."""

    e164: str | None = None                 # "+905551234567" (normalize)
    country: str | None = None              # ISO 3166-1 alpha-2: "TR", "US", "DE"
    country_code: int | None = None         # 90, 1, 49
    local_format: str | None = None         # "0555 123 45 67"
    carrier: str | None = None              # "Turkcell" (numverify gerekir)
    line_type: str | None = None            # "mobile" | "landline" | "voip" | "fixed_voip"
    valid: bool | None = None


@dataclass(frozen=True)
class OSINTEmailSignals:
    """Email OSINT sinyalleri (holehe-api)."""

    domain: str | None = None               # "onurinsaat.com.tr"
    domain_type: str | None = None          # "corporate" | "freemail" | "disposable" | "unknown"
    registered_sites: list[str] = field(default_factory=list)  # exists=True olan siteler
    site_count: int = 0                     # len(registered_sites)
    rate_limited_count: int = 0             # kaç modül rate limit dönmüş
    error_count: int = 0                    # kaç modül hata dönmüş
    modules_checked: int = 0                # toplam test edilen modül
    any_rate_limited: bool = False


@dataclass(frozen=True)
class OSINTProfile:
    """Birleştirilmiş OSINT profili. Tek tenant için tek email/telefon kombinasyonuna ait."""

    provider: str                           # "self_hosted" | "stub" | future "pdl", "twilio"
    fetched_at: datetime                    # UTC
    phone: OSINTPhoneSignals = field(default_factory=OSINTPhoneSignals)
    email: OSINTEmailSignals = field(default_factory=OSINTEmailSignals)
    notes: list[str] = field(default_factory=list)  # human-readable flags
    cache_hit: bool = False                 # DBCachedOSINTAdapter tarafından set edilir
    elapsed_ms: int = 0                     # upstream fetch süresi (cache hit=0)

    def with_cache_hit(self, hit: bool) -> OSINTProfile:
        """Yeni profile ile cache_hit flag'i güncellenmiş olanı döner (frozen copy)."""
        return replace(self, cache_hit=hit)

    def to_dict(self) -> dict[str, Any]:
        """JSONB serialize için kullanılan flat dict (DB repo'da consume edilir)."""
        return {
            "provider": self.provider,
            "fetched_at": self.fetched_at.isoformat(),
            "cache_hit": self.cache_hit,
            "elapsed_ms": self.elapsed_ms,
            "phone": {
                "e164": self.phone.e164,
                "country": self.phone.country,
                "country_code": self.phone.country_code,
                "local_format": self.phone.local_format,
                "carrier": self.phone.carrier,
                "line_type": self.phone.line_type,
                "valid": self.phone.valid,
            },
            "email": {
                "domain": self.email.domain,
                "domain_type": self.email.domain_type,
                "registered_sites": list(self.email.registered_sites),
                "site_count": self.email.site_count,
                "rate_limited_count": self.email.rate_limited_count,
                "error_count": self.email.error_count,
                "modules_checked": self.email.modules_checked,
                "any_rate_limited": self.email.any_rate_limited,
            },
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OSINTProfile:
        """DB'den okunan JSONB'yi parse eder (to_dict() tersi)."""
        phone = data.get("phone") or {}
        email = data.get("email") or {}
        fetched = data.get("fetched_at")
        if isinstance(fetched, str):
            # "Z" suffix fromisoformat <3.11'de sorun; strip edip parse
            fetched = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
        elif fetched is None:
            fetched = datetime.now(UTC)
        return cls(
            provider=data.get("provider", "unknown"),
            fetched_at=fetched,
            cache_hit=bool(data.get("cache_hit", False)),
            elapsed_ms=int(data.get("elapsed_ms", 0)),
            phone=OSINTPhoneSignals(
                e164=phone.get("e164"),
                country=phone.get("country"),
                country_code=phone.get("country_code"),
                local_format=phone.get("local_format"),
                carrier=phone.get("carrier"),
                line_type=phone.get("line_type"),
                valid=phone.get("valid"),
            ),
            email=OSINTEmailSignals(
                domain=email.get("domain"),
                domain_type=email.get("domain_type"),
                registered_sites=list(email.get("registered_sites") or []),
                site_count=int(email.get("site_count") or 0),
                rate_limited_count=int(email.get("rate_limited_count") or 0),
                error_count=int(email.get("error_count") or 0),
                modules_checked=int(email.get("modules_checked") or 0),
                any_rate_limited=bool(email.get("any_rate_limited", False)),
            ),
            notes=list(data.get("notes") or []),
        )


class OSINTError(Exception):
    """Adapter seviyesinde generic hata — caller stub profile'a fallback edebilir."""


class OSINTTimeout(OSINTError):
    """Upstream timeout (per-call veya total)."""


class OSINTPort(ABC):
    """OSINT enrichment port.

    Tüm adapter'lar `OSINTProfile` döndürmelidir. Upstream hatalarını
    `OSINTError`/`OSINTTimeout` olarak yükseltin; caller (PreScoreService)
    stub profile'a fallback eder ve `notes`'a hata sebebini ekler.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter adı — metriklerde label, DB'de `provider` kolonunda saklanır."""

    @abstractmethod
    async def enrich(
        self,
        *,
        email: str | None,
        phone: str | None,
        name: str | None = None,
        tenant_id: UUID | None = None,
    ) -> OSINTProfile:
        """Lead'in email+phone'undan OSINT profili üretir.

        `email` ve `phone` ikisi de None olursa adapter boş profile dönmelidir
        (stub equivalent). `tenant_id` DBCachedOSINTAdapter için zorunlu;
        upstream-only adapter'lar (self_hosted, stub) tenant_id'yi yok sayar.

        Raises:
            OSINTError / OSINTTimeout: upstream beklenen recoverable hatalar.
              Non-recoverable hatalar (kod bug) normal Exception olarak geçer.
        """
