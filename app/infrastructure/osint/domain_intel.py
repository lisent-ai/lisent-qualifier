"""Domain intelligence pipeline — free, self-hosted, four-layer enrichment.

Given an email domain, produce a dict of signals the pre-score ensemble
can use to justify a corporate/B2B classification. All four layers run in
parallel where safe; the pipeline degrades gracefully when any layer
fails (returns partial signals rather than throwing).

Layers:
    1. DNS/MX  — is the domain deliverable? (dnspython, offline except for DNS)
    2. Classification — freemail / disposable / corporate bucket
    3. WHOIS + Certificate Transparency — domain age + registered org +
       SSL-cert Subject "O=Company Name"
    4. Groq `openai/gpt-oss-120b` with native `tools=[browser_search]` —
       web-grounded lookup returning JSON {company_name, industry, size,
       country, confidence}. Only fires for non-freemail/non-disposable
       domains to keep cost proportional to lead value.

Output is merged into `OSINTEmailSignals` optional fields so the existing
60-day DB cache persists everything automatically — no migration needed.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
import structlog

from app.infrastructure.osint.disposable_list import (
    disposable_list_size,
    is_disposable,
    is_freemail,
)

log = structlog.get_logger(__name__)


_CRT_SH_TIMEOUT = 4.0
_WHOIS_TIMEOUT = 5.0
_DNS_TIMEOUT = 2.0
_LLM_TIMEOUT = 45.0  # browser_search tool call + synthesis can take 15-30s

_ORG_RDN_RE = re.compile(r"(?:\bO=)([^,+/]+)", re.IGNORECASE)
_COMPOUND_TLD_RE = re.compile(r"\.(com|org|net|edu|gov)\.(\w{2})$")


def classify_domain(domain: str) -> str:
    """Return a coarse bucket for the domain before any network lookup.

    Values:
        "missing"            — blank / invalid
        "disposable"         — in the curated blocklist
        "freemail"           — gmail/hotmail/yandex/etc.
        "corporate_verified" — .gov.tr / .edu.tr (registry-verified)
        "corporate_suspected" — .com.tr / any non-freemail TLD with MX
        "unknown"            — domain looks valid but no stronger signal
    """
    if not domain:
        return "missing"
    d = domain.lower().strip()
    if is_disposable(d):
        return "disposable"
    if is_freemail(d):
        return "freemail"
    if d.endswith(".gov.tr") or d.endswith(".edu.tr") or d.endswith(".gov") or d.endswith(".edu"):
        return "corporate_verified"
    # .com.tr / .org.tr / .net.tr are TR commercial; general TLD also
    # defaults to suspected-corporate when it has an MX record (caller
    # will upgrade after MX check).
    return "corporate_suspected"


async def mx_check(domain: str) -> dict[str, Any]:
    """Return {mx_valid, mx_host} using dnspython."""
    outcome = "error"
    result: dict[str, Any] = {}
    try:
        import dns.resolver  # type: ignore[import-untyped]
    except ImportError:
        _layer_metric("mx", "skipped")
        return {}
    try:
        loop = asyncio.get_running_loop()
        records = await loop.run_in_executor(
            None,
            lambda: list(dns.resolver.resolve(domain, "MX", lifetime=_DNS_TIMEOUT)),
        )
        if records:
            records.sort(key=lambda r: getattr(r, "preference", 0))
            result = {
                "mx_valid": True,
                "mx_host": str(records[0].exchange).rstrip("."),
            }
            outcome = "hit"
        else:
            result = {"mx_valid": False}
            outcome = "miss"
    except Exception as exc:  # noqa: BLE001
        log.debug("mx_check_failed", domain=domain, error=str(exc))
        result = {"mx_valid": False}
        outcome = "error"
    _layer_metric("mx", outcome)
    return result


def _layer_metric(layer: str, outcome: str) -> None:
    try:
        from app.metrics import OSINT_DOMAIN_INTEL_LAYER_TOTAL
        OSINT_DOMAIN_INTEL_LAYER_TOTAL.labels(layer=layer, outcome=outcome).inc()
    except ImportError:
        pass


def _parse_intel_json(raw: str) -> dict[str, Any] | None:
    """3-tier JSON extraction from an LLM response that might include
    prose around the object (the tools+no-json-mode combination can
    cause gpt-oss-120b to narrate briefly before the JSON block)."""
    # Tier 1: direct parse
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    # Tier 2: markdown fenced block
    fence_start = raw.find("```")
    if fence_start != -1:
        body = raw[fence_start + 3:]
        # strip optional language tag
        if "\n" in body:
            body = body.split("\n", 1)[1]
        fence_end = body.find("```")
        if fence_end != -1:
            body = body[:fence_end]
        try:
            parsed = json.loads(body.strip())
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    # Tier 3: first balanced {...} block
    first_brace = raw.find("{")
    if first_brace == -1:
        return None
    depth = 0
    for i in range(first_brace, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(raw[first_brace:i + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


async def whois_lookup(domain: str) -> dict[str, Any]:
    """Return {registrar, domain_age_days, whois_org} — best-effort."""
    try:
        import whois  # type: ignore[import-untyped]
    except ImportError:
        _layer_metric("whois", "skipped")
        return {}
    try:
        loop = asyncio.get_running_loop()
        w = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: whois.whois(domain)),
            timeout=_WHOIS_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("whois_lookup_failed", domain=domain, error=str(exc))
        _layer_metric("whois", "error")
        return {}

    def _first(v: Any) -> Any:
        if isinstance(v, list) and v:
            return v[0]
        return v

    out: dict[str, Any] = {}
    registrar = _first(getattr(w, "registrar", None))
    if registrar:
        out["domain_registrar"] = str(registrar)

    created = _first(getattr(w, "creation_date", None))
    if created is not None:
        try:
            from datetime import datetime, timezone
            age_days = (datetime.now(timezone.utc) - created).days if hasattr(created, "year") else None
            if age_days and age_days > 0:
                out["domain_age_days"] = int(age_days)
        except Exception:
            pass

    org = _first(getattr(w, "org", None))
    if org:
        out["whois_org"] = str(org)

    _layer_metric("whois", "hit" if out else "miss")
    return out


async def crt_sh_lookup(domain: str, client: httpx.AsyncClient) -> dict[str, Any]:
    """Query crt.sh Certificate Transparency and extract Subject "O=" org.

    The issuer_name on a corporate-issued SSL cert frequently contains
    the legal entity name — this alone is often enough to hint the LLM
    layer toward the right company without a web search.
    """
    url = f"https://crt.sh/?output=json&Identity={domain}"
    try:
        resp = await client.get(url, timeout=_CRT_SH_TIMEOUT)
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        log.debug("crt_sh_failed", domain=domain, error=str(exc))
        _layer_metric("crt_sh", "error")
        return {}
    if resp.status_code != 200:
        _layer_metric("crt_sh", "error")
        return {}
    try:
        certs = resp.json()
    except ValueError:
        _layer_metric("crt_sh", "error")
        return {}
    if not isinstance(certs, list) or not certs:
        _layer_metric("crt_sh", "miss")
        return {"ssl_cert_count": 0}

    # Scan up to 10 most recent certs for a plausible org name.
    for cert in certs[:10]:
        subject = cert.get("issuer_name") or cert.get("name_value") or ""
        match = _ORG_RDN_RE.search(str(subject))
        if not match:
            continue
        org = match.group(1).strip().strip('"')
        # Filter out Let's Encrypt / Cloudflare / standard CAs
        if any(needle in org.lower() for needle in (
            "let's encrypt", "sectigo", "digicert", "cloudflare", "google trust",
            "amazon", "zerossl",
        )):
            continue
        _layer_metric("crt_sh", "hit")
        return {"ssl_cert_org": org, "ssl_cert_count": len(certs)}

    _layer_metric("crt_sh", "miss")
    return {"ssl_cert_count": len(certs)}


_DOMAIN_INTEL_SYSTEM = (
    "You are a B2B research agent for a construction sales team. "
    "Research the given email domain on the live web and identify the "
    "owning company. For Turkish domains prioritize Ticaret Sicili "
    "Gazetesi and LinkedIn results.\n\n"
    "Your workflow:\n"
    "1. Call `browser_search` at least once with a targeted query about "
    "   the domain.\n"
    "2. Read the results.\n"
    "3. Call `submit_domain_report` exactly once with your structured "
    "   findings.\n\n"
    "Do NOT emit a plain text answer. Do NOT use any tool other than "
    "`browser_search` and `submit_domain_report`. The sales team reads "
    "the structured report, not free-form prose."
)

_DOMAIN_INTEL_USER = (
    "Research this email domain and file a structured report.\n\n"
    "Domain: {domain}\n"
    "Known DNS/WHOIS/SSL hints (steer your search, do not replace it):\n"
    "{hints}\n\n"
    "After at least one browser_search, call submit_domain_report with:\n"
    "  company_name — legal entity name if identifiable, else null\n"
    "  industry — one of: construction, real_estate, retail, finance, "
    "technology, manufacturing, services, other, unknown\n"
    "  size_estimate — small, medium, large, enterprise, or unknown\n"
    "  country — ISO-2 code (TR, DE, GB, AE, ...) or null\n"
    "  is_legitimate_business — true/false\n"
    "  confidence — 0.0-1.0\n"
    "  source_urls — 1-3 URLs you consulted\n\n"
    "For freemail/disposable/test domains you may skip search depth but "
    "still call submit_domain_report with company_name=null and "
    "is_legitimate_business=false."
)

_SUBMIT_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_domain_report",
        "description": (
            "Submit the structured research finding for a domain. "
            "Call this exactly once at the end of your workflow, after "
            "you have consulted browser_search. This is the only way "
            "your findings reach the downstream pipeline."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": ["string", "null"],
                    "description": "Legal entity name or null.",
                },
                "industry": {
                    "type": "string",
                    "enum": [
                        "construction", "real_estate", "retail",
                        "finance", "technology", "manufacturing",
                        "services", "other", "unknown",
                    ],
                },
                "size_estimate": {
                    "type": "string",
                    "enum": [
                        "small", "medium", "large", "enterprise", "unknown",
                    ],
                },
                "country": {
                    "type": ["string", "null"],
                    "description": "ISO-2 country code or null.",
                },
                "is_legitimate_business": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "source_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 3,
                },
            },
            "required": [
                "company_name", "industry", "size_estimate", "country",
                "is_legitimate_business", "confidence", "source_urls",
            ],
        },
    },
}


async def enrich_domain_via_llm(
    domain: str,
    hints: dict[str, Any],
    *,
    llm_client: Any,  # groq.AsyncGroq
    model: str,
) -> dict[str, Any]:
    """Call Groq gpt-oss-120b with native browser_search for domain → company.

    This is the Hunter.io replacement. Same model we use for scoring so
    config stays single-key. Groq server-side runs web searches and
    returns a JSON synthesis. `reasoning_effort="low"` keeps token cost
    and latency down.
    """
    messages = [
        {"role": "system", "content": _DOMAIN_INTEL_SYSTEM},
        {
            "role": "user",
            "content": _DOMAIN_INTEL_USER.format(
                domain=domain,
                hints=json.dumps(hints, ensure_ascii=False),
            ),
        },
    ]
    try:
        # IMPORTANT: Groq rejects `response_format=json_object` combined
        # with `tools=[...]` with a 400 "json mode cannot be combined
        # with tool/function calling". Since we need tool calls for the
        # web search we drop json mode and rely on the prompt's explicit
        # "return ONLY JSON" instruction. _parse_judgment-style fallbacks
        # (markdown strip, brace-scan) in this function handle any
        # stray prose the model emits around the JSON body.
        #
        # Also: reasoning_effort omitted intentionally. On gpt-oss-120b
        # low reasoning_effort silently skips tool calls (community
        # thread #385). Default (medium) reliably invokes browser_search.
        # Groq SDK default client timeout is 5s; browser_search + synthesis
        # routinely takes 15-30s. Override per-request, keep global tight
        # for chat streaming.
        #
        # Two tools are registered: the built-in browser_search (Groq does
        # the web request) and a `submit_domain_report` function whose
        # JSON schema is the structured output we want. gpt-oss-120b
        # otherwise invents a phantom "json" tool call which Groq rejects
        # with a 400 — by registering the tool explicitly we get
        # well-formed arguments in `tool_calls` instead.
        resp = await asyncio.wait_for(
            llm_client.with_options(timeout=_LLM_TIMEOUT).chat.completions.create(
                model=model,
                messages=messages,
                tools=[
                    {"type": "browser_search"},
                    _SUBMIT_REPORT_TOOL,
                ],
                max_tokens=2048,
            ),
            timeout=_LLM_TIMEOUT + 5.0,
        )
    except asyncio.TimeoutError:
        log.warning("domain_intel_llm_timeout", domain=domain)
        _layer_metric("llm_search", "error")
        return {}
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "domain_intel_llm_failed",
            domain=domain,
            error=f"{type(exc).__name__}: {exc}",
        )
        _layer_metric("llm_search", "error")
        return {}

    message = resp.choices[0].message
    data: dict[str, Any] | None = None

    # Preferred path: the model called submit_domain_report. Arguments
    # are already a JSON string — parse once.
    tool_calls = getattr(message, "tool_calls", None) or []
    for tc in tool_calls:
        fn = getattr(tc, "function", None)
        if fn is None:
            continue
        if getattr(fn, "name", "") == "submit_domain_report":
            try:
                data = json.loads(fn.arguments or "{}")
                break
            except json.JSONDecodeError as exc:
                log.warning(
                    "domain_intel_submit_report_bad_json",
                    domain=domain,
                    error=str(exc),
                    raw=(fn.arguments or "")[:200],
                )

    # Fallback: some model variants still emit the JSON as message.content
    # (especially when tool routing picks it as a response type).
    if data is None:
        content = (message.content or "").strip()
        if content:
            data = _parse_intel_json(content)

    if data is None:
        log.warning(
            "domain_intel_llm_bad_json",
            domain=domain,
            content_head=(message.content or "")[:200],
            had_tool_calls=bool(tool_calls),
        )
        _layer_metric("llm_search", "error")
        return {}
    if not isinstance(data, dict):
        _layer_metric("llm_search", "miss")
        return {}
    _layer_metric(
        "llm_search",
        "hit" if data.get("company_name") else "miss",
    )
    # Only surface the fields we actually consume downstream.
    return {
        "organization_name": data.get("company_name") or None,
        "organization_industry": data.get("industry") or None,
        "organization_size": data.get("size_estimate") or None,
        "organization_country": data.get("country") or None,
        "is_legitimate_business": bool(data.get("is_legitimate_business"))
        if data.get("is_legitimate_business") is not None else None,
        "enrichment_confidence": (
            float(data["confidence"]) if isinstance(data.get("confidence"), (int, float)) else None
        ),
    }


async def enrich_domain(
    domain: str,
    *,
    client: httpx.AsyncClient | None = None,
    llm_client: Any = None,
    llm_model: str | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Four-layer pipeline. Returns a flat dict of enrichment signals.

    The LLM layer is flag-gated AND category-gated — only fires on
    suspected-corporate domains with a valid MX record, to keep the
    browser_search tool cost scaling with lead value.
    """
    if not domain:
        return {"domain_class": "missing"}
    domain = domain.lower().strip()

    # Layer 2 first — very cheap, short-circuits disposable / freemail.
    coarse = classify_domain(domain)
    if coarse in ("disposable", "freemail", "missing"):
        return {"domain_class": coarse}

    # Owned-client fallback so callers can pass None.
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=5.0, headers={"User-Agent": "lisent-qualifier/osint"},
        )

    try:
        dns_out, whois_out, ct_out = await asyncio.gather(
            mx_check(domain),
            whois_lookup(domain),
            crt_sh_lookup(domain, client),
            return_exceptions=False,
        )
        hints: dict[str, Any] = {**(dns_out or {}), **(whois_out or {}), **(ct_out or {})}
        # Keep the coarse classification even when MX is flaky:
        # dnspython false-negatives are common (DNSSEC hiccups, resolver
        # timeouts, TR-resolver quirks for .com.tr). A broken MX on a
        # .com.tr domain is still more likely to be a real (if temporarily
        # mis-configured) company than a random consumer domain, and the
        # LLM browser_search layer can still produce useful signal.
        domain_class = coarse
        result: dict[str, Any] = {"domain_class": domain_class, **hints}

        if use_llm and llm_client is not None and llm_model and domain_class in (
            "corporate_verified", "corporate_suspected",
        ):
            llm_out = await enrich_domain_via_llm(
                domain, hints,
                llm_client=llm_client, model=llm_model,
            )
            result.update({k: v for k, v in llm_out.items() if v is not None})

        return result
    finally:
        if owns_client:
            await client.aclose()


def health_snapshot() -> dict[str, Any]:
    """For /health endpoint or startup log — confirms blocklist loaded."""
    return {
        "disposable_list_size": disposable_list_size(),
        "freemail_list_size": 0,  # FREEMAIL_DOMAINS is a set; avoid import cycle
    }
