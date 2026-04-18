"""
CTA (call-to-action) routing for qualified lead handoff.

Three bands, decided by composite score + persuadability signals:
  - CYPRUS_VISIT: high score (>= visit_threshold) → invite on-site viewing
  - CALENDLY:    mid score (>= medium_floor) AND persuadable → online meeting
  - NURTURE:     low score or low potential → soft close, no CTA

Persuadability (for the mid band):
  engagement_score >= 40
  AND negative_signal_count <= 1
  AND dominant intent ∈ {buying_signal, info_seeking, urgency}
"""
from __future__ import annotations

from enum import StrEnum


class CTAType(StrEnum):
    CYPRUS_VISIT = "cyprus_visit"
    CALENDLY = "calendly"
    NURTURE = "nurture"


class QualificationPotential(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


_PERSUADABLE_INTENTS = frozenset({"buying_signal", "info_seeking", "urgency"})

DEFAULT_MEDIUM_FLOOR = 50


def _is_persuadable(
    engagement_score: int,
    negative_signal_count: int,
    dominant_intent: str | None,
) -> bool:
    if engagement_score < 40:
        return False
    if negative_signal_count > 1:
        return False
    if dominant_intent and dominant_intent in _PERSUADABLE_INTENTS:
        return True
    # Absent explicit intent we still allow persuasion provided other gates pass.
    return dominant_intent is None


def route_cta(
    *,
    score: int,
    visit_threshold: int,
    medium_floor: int = DEFAULT_MEDIUM_FLOOR,
    engagement_score: int = 0,
    negative_signal_count: int = 0,
    dominant_intent: str | None = None,
    judge_recommendation: str | None = None,
) -> tuple[CTAType, QualificationPotential]:
    """
    Return (cta_type, qualification_potential).

    Router has final say; judge_recommendation is treated as advisory and only
    applied when score is on a band boundary (within 2 points), to avoid
    whiplash from LLM noise.
    """
    if score >= visit_threshold:
        return CTAType.CYPRUS_VISIT, QualificationPotential.HIGH

    if score >= medium_floor and _is_persuadable(
        engagement_score, negative_signal_count, dominant_intent
    ):
        # Judge may downgrade at boundary (e.g. poor tenor despite score).
        if judge_recommendation == "nurture" and score <= medium_floor + 2:
            return CTAType.NURTURE, QualificationPotential.LOW
        return CTAType.CALENDLY, QualificationPotential.MEDIUM

    # Below medium floor OR above it but not persuadable.
    # Judge may upgrade a just-below-floor lead it sees potential in.
    if (
        judge_recommendation == "calendly"
        and medium_floor - 3 <= score < medium_floor
        and _is_persuadable(engagement_score, negative_signal_count, dominant_intent)
    ):
        return CTAType.CALENDLY, QualificationPotential.MEDIUM

    return CTAType.NURTURE, QualificationPotential.LOW
