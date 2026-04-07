"""
Lead enrichment — fill empty lead fields from LLM extraction data.

When a lead arrives with unknown/empty fields (budget_range="unknown",
project_type="other", city="" etc.), the LLM Judge extracts this information
during CHAMP scoring. This module maps those extracted fields back to the
lead data structure.

Uses LLM-extracted structured fields (extracted_budget_range, extracted_city, etc.)
from QualificationJudgmentResult — NOT regex/keyword parsing.

Pure domain logic — no I/O.
"""
from __future__ import annotations

from typing import Any


def enrich_lead_from_extraction(
    lead_json: dict[str, Any],
    champ_json: dict[str, Any] | None,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Fill empty/unknown lead fields from LLM Judge extraction results.

    The Judge extracts structured fields like extracted_budget_range,
    extracted_city, etc. during qualification scoring. This function
    maps them back to the lead data if the original fields were empty.

    Returns a new dict with enriched data. Original lead_json is NOT mutated.
    Includes an ``enriched_fields`` list showing which fields were filled by AI.
    """
    enriched = {**lead_json}
    enriched_fields: list[str] = []

    if not champ_json:
        enriched["enriched_fields"] = enriched_fields
        return enriched

    # Collect all text for fallback keyword extraction
    all_notes = _collect_notes(champ_json)
    conversation_text = ""
    if messages:
        conversation_text = " ".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
    search_text = (all_notes + " " + conversation_text).lower()

    # ── Budget enrichment ───────────────────────────────────────────────
    if _is_empty(enriched.get("budget_range"), "unknown"):
        extracted = champ_json.get("extracted_budget_range", "")
        if not extracted or extracted == "unknown":
            # Fallback: extract from reasoning text
            _, extracted = _extract_budget_from_text(search_text)
        if extracted and extracted != "unknown":
            enriched["budget_range"] = extracted
            enriched_fields.append("budget_range")

    if not enriched.get("budget_amount"):
        extracted = champ_json.get("extracted_budget_amount")
        if not extracted:
            extracted, _ = _extract_budget_from_text(search_text)
        if extracted:
            enriched["budget_amount"] = extracted
            enriched_fields.append("budget_amount")

    # ── Project type enrichment ─────────────────────────────────────────
    if _is_empty(enriched.get("project_type"), "other"):
        extracted = champ_json.get("extracted_project_type", "")
        if not extracted or extracted == "other":
            extracted = _detect_project_type(search_text)
        if extracted and extracted != "other":
            enriched["project_type"] = extracted
            enriched_fields.append("project_type")

    # ── Timeline enrichment ─────────────────────────────────────────────
    if _is_empty(enriched.get("timeline_urgency"), "unknown"):
        extracted = champ_json.get("extracted_timeline_urgency", "")
        if not extracted or extracted == "unknown":
            extracted = _detect_timeline(search_text)
        if extracted and extracted != "unknown":
            enriched["timeline_urgency"] = extracted
            enriched_fields.append("timeline_urgency")

    # ── Authority enrichment ────────────────────────────────────────────
    if _is_empty(enriched.get("decision_authority"), "unknown"):
        extracted = champ_json.get("extracted_decision_authority", "")
        if not extracted or extracted == "unknown":
            extracted = _detect_authority(search_text)
        if extracted and extracted != "unknown":
            enriched["decision_authority"] = extracted
            enriched_fields.append("decision_authority")

    # ── City enrichment ─────────────────────────────────────────────────
    contact = enriched.get("contact", {})
    if isinstance(contact, dict) and not contact.get("city"):
        extracted = champ_json.get("extracted_city", "")
        if not extracted:
            extracted = _extract_city(search_text)
        if extracted:
            enriched["contact"] = {**contact, "city": extracted}
            enriched_fields.append("contact.city")

    # ── Project details enrichment ──────────────────────────────────────
    extracted_details = champ_json.get("extracted_project_details", "")
    if extracted_details:
        existing_notes = enriched.get("notes", "")
        if not existing_notes:
            enriched["notes"] = extracted_details
            enriched_fields.append("notes")
        elif extracted_details not in existing_notes:
            enriched["notes"] = f"{existing_notes}\n\nAI Analiz: {extracted_details}"
            enriched_fields.append("notes")

    # ── Sector qualifiers (from judge) ──────────────────────────────────
    sq = champ_json.get("sector_qualifiers", {})
    if sq and any(v is not None for v in sq.values()):
        enriched["sector_qualifiers"] = sq
        enriched_fields.append("sector_qualifiers")

    enriched["enriched_fields"] = enriched_fields
    return enriched


def _collect_notes(champ_json: dict[str, Any]) -> str:
    """Collect all text notes/reasoning from CHAMP/Judge extraction."""
    parts: list[str] = []
    for key in (
        "challenges_notes", "authority_notes", "money_notes", "prioritization_notes",
        "challenges_reasoning", "authority_reasoning", "money_reasoning",
        "prioritization_reasoning", "holistic_reasoning", "icp_fit_assessment",
    ):
        val = champ_json.get(key, "")
        if val:
            parts.append(val)
    return " ".join(parts)


def _is_empty(value: Any, empty_sentinel: str = "") -> bool:
    """Check if a field value is empty or contains a sentinel 'unknown' value."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value.strip().lower() == empty_sentinel.lower()
    return False


# ── Fallback extraction helpers (keyword-based, from reasoning text) ────────

import re

_BUDGET_PATTERNS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"(\d+)\s*milyon", re.IGNORECASE), 1_000_000),
    (re.compile(r"(\d+)\s*million", re.IGNORECASE), 1_000_000),
    (re.compile(r"(\d+)\s*m\s*tl", re.IGNORECASE), 1_000_000),
    (re.compile(r"(\d+)\s*bin", re.IGNORECASE), 1_000),
]

_BUDGET_RANGE_MAP: list[tuple[int, str]] = [
    (10_000_000, "over_10m"),
    (3_000_000, "3m_10m"),
    (1_000_000, "1m_3m"),
    (500_000, "500k_1m"),
    (0, "under_500k"),
]


def _extract_budget_from_text(text: str) -> tuple[int | None, str]:
    for pattern, multiplier in _BUDGET_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                amount = int(match.group(1)) * multiplier
                for threshold, range_name in _BUDGET_RANGE_MAP:
                    if amount >= threshold:
                        return amount, range_name
            except (ValueError, IndexError):
                continue
    return None, ""


_PROJECT_KEYWORDS: dict[str, list[str]] = {
    "commercial": ["otel", "hotel", "plaza", "ofis", "office", "magaza", "restoran", "cafe"],
    "industrial": ["fabrika", "factory", "depo", "warehouse", "sanayi"],
    "residential": ["villa", "konut", "ev", "daire", "apartman", "residence", "rezidans", "mustakil", "penthouse"],
    "renovation": ["tadilat", "renovasyon", "restorasyon", "yenileme"],
    "land": ["arsa", "arazi", "land", "parsel"],
}

_TIMELINE_KEYWORDS: dict[str, list[str]] = {
    "immediate": ["hemen", "acil", "bu hafta", "bu ay", "derhal"],
    "short": ["gelecek ay", "1 ay", "2 ay", "3 ay", "yaz basi", "yaz başı", "bahar"],
    "medium": ["6 ay", "yil sonu", "yıl sonu", "bu yil", "bu yıl"],
    "long": ["gelecek yil", "gelecek yıl", "2 yil"],
}

_AUTHORITY_KEYWORDS: dict[str, list[str]] = {
    "sole": ["ben karar", "benim karar", "tek yetkili"],
    "joint": ["ortaklarim", "esimle", "eşimle", "birlikte karar", "biz karar", "ortak karar"],
    "influencer": ["patronum", "mudur", "müdür", "komite", "soracagim", "soracağım"],
}

_TURKISH_CITIES: list[str] = [
    "istanbul", "ankara", "izmir", "antalya", "bursa", "adana", "konya",
    "gaziantep", "mersin", "kayseri", "trabzon", "samsun", "denizli",
    "mugla", "muğla", "aydin", "aydın", "bodrum", "fethiye", "alanya",
    "beylikduzu", "beylikdüzü", "kadikoy", "kadıköy", "besiktas", "beşiktaş",
    "uskudar", "üsküdar", "bakirkoy", "bakırköy", "sisli", "şişli",
    "north cyprus", "kuzey kibris", "kuzey kıbrıs", "girne", "lefkosa",
    "lefkoşa", "gazimagusa", "gazimağusa", "iskele",
]


def _detect_project_type(text: str) -> str:
    for category, keywords in _PROJECT_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return ""


def _detect_timeline(text: str) -> str:
    for category, keywords in _TIMELINE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return ""


def _detect_authority(text: str) -> str:
    for category, keywords in _AUTHORITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return ""


def _extract_city(text: str) -> str:
    for city in _TURKISH_CITIES:
        if city in text:
            return city.title()
    return ""
