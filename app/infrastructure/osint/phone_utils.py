"""Offline phone enrichment via Google's libphonenumber (Python port).

Global coverage — the `phonenumbers` package bundles ~1 MiB of ITU +
telco + regulator-sourced metadata for 240+ country codes, so the same
call works for Turkish, German, Emirati, British, etc. numbers without
any network request.

Carrier accuracy in MNP (Mobile Number Portability) countries like TR /
UK / DE / US reflects the *original* prefix-owner. This is acceptable
for pre-scoring — we only need "is this a TR mobile?" / "what operator
range?" signal, not a billing-grade lookup. Paid APIs are for audit /
compliance, not sales prep.

Used by `SelfHostedOSINTAdapter._merge` to fill `carrier` and
`line_type` when PhoneInfoga's local scanner returns them as None
(which is always, without a paid NUMVERIFY_API_KEY).
"""
from __future__ import annotations

from typing import Any

import structlog

try:
    import phonenumbers
    from phonenumbers import carrier, phonenumberutil
except ImportError:  # pragma: no cover — should be installed via pyproject
    phonenumbers = None  # type: ignore[assignment]
    carrier = None  # type: ignore[assignment]
    phonenumberutil = None  # type: ignore[assignment]

log = structlog.get_logger(__name__)


_LINE_TYPE_MAP: dict[int, str] = {}


def _build_line_type_map() -> dict[int, str]:
    """Lazy-build because phonenumberutil may be absent in dev envs."""
    if phonenumberutil is None:
        return {}
    if _LINE_TYPE_MAP:
        return _LINE_TYPE_MAP
    t = phonenumberutil.PhoneNumberType
    _LINE_TYPE_MAP.update({
        t.MOBILE: "mobile",
        t.FIXED_LINE: "landline",
        t.FIXED_LINE_OR_MOBILE: "mobile_or_landline",
        t.VOIP: "voip",
        t.PREMIUM_RATE: "premium",
        t.TOLL_FREE: "toll_free",
        t.SHARED_COST: "shared_cost",
        t.PERSONAL_NUMBER: "personal",
        t.PAGER: "pager",
        t.UAN: "uan",
        t.VOICEMAIL: "voicemail",
        t.UNKNOWN: "unknown",
    })
    return _LINE_TYPE_MAP


def enrich_phone_offline(raw: str | None) -> dict[str, Any]:
    """Parse an E.164-or-international phone string and return enrichment.

    Args:
        raw: input phone. Accepts `+905551234567`, `05551234567` (+ TR
            hint), `+49 151 123 45678`, etc. If None or empty → empty
            result.

    Returns:
        dict with (all optional):
            valid:        bool
            country:      ISO-2 region code (TR, DE, GB, AE, …)
            country_code: int (+90 → 90)
            carrier:      str | None  — e.g. "Turkcell", "Vodafone"
            line_type:    str         — mobile / landline / voip / …
            e164:         str         — normalized "+905551234567"
    """
    if not raw or phonenumbers is None:
        return {}
    digits = raw.strip()
    if not digits:
        return {}

    # Try strict E.164 first; fall back to a region hint if the user
    # submitted a local format. TR is the most common fallback in this
    # codebase; unknown-prefix numbers will fail both attempts and
    # return an invalid dict so the caller can treat them as "unverified".
    pn = None
    for region_hint in (None, "TR"):
        try:
            pn = phonenumbers.parse(digits, region_hint)
            break
        except phonenumbers.NumberParseException:
            continue

    if pn is None or not phonenumbers.is_valid_number(pn):
        return {"valid": False}

    line_type_map = _build_line_type_map()
    ntype = phonenumberutil.number_type(pn)
    carrier_name: str | None = None
    try:
        # Prefer "en"; carrier metadata uses Latin names for all regions.
        carrier_name = carrier.name_for_number(pn, "en") or None
    except Exception as exc:  # pragma: no cover — metadata lookup edge
        log.debug("phonenumbers_carrier_lookup_failed", raw=raw[:12], error=str(exc))

    return {
        "valid": True,
        "country": phonenumbers.region_code_for_number(pn),
        "country_code": pn.country_code,
        "carrier": carrier_name,
        "line_type": line_type_map.get(ntype, "unknown"),
        "e164": phonenumbers.format_number(
            pn, phonenumbers.PhoneNumberFormat.E164,
        ),
    }
