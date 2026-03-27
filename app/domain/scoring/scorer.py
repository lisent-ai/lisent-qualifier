"""
RuleBasedScorer — pure functions, zero I/O.

Scoring breakdown (total 100):
  Budget       : 30 pts  (highest weight — construction is budget-gated)
  Timeline     : 25 pts  (urgent projects close faster)
  Project Type : 20 pts  (commercial/industrial are higher value)
  Authority    : 15 pts  (decision maker matters)
  Data Quality : 10 pts  (completeness of form)
"""
from app.domain.lead.entities import Lead
from app.domain.lead.enums import (
    BudgetRange,
    DecisionAuthority,
    ProjectType,
    TimelineUrgency,
)

# ── Budget (30 pts) ──────────────────────────────────────────────────────────
_BUDGET_SCORES: dict[BudgetRange, int] = {
    BudgetRange.OVER_10M: 30,
    BudgetRange.RANGE_3M_10M: 25,
    BudgetRange.RANGE_1M_3M: 18,
    BudgetRange.RANGE_500K_1M: 10,
    BudgetRange.UNDER_500K: 4,
    BudgetRange.UNKNOWN: 0,
}

# ── Timeline (25 pts) ─────────────────────────────────────────────────────────
_TIMELINE_SCORES: dict[TimelineUrgency, int] = {
    TimelineUrgency.IMMEDIATE: 25,
    TimelineUrgency.SHORT: 18,
    TimelineUrgency.MEDIUM: 10,
    TimelineUrgency.LONG: 4,
    TimelineUrgency.UNKNOWN: 0,
}

# ── Project Type (20 pts) ─────────────────────────────────────────────────────
_PROJECT_SCORES: dict[ProjectType, int] = {
    ProjectType.COMMERCIAL: 20,
    ProjectType.INDUSTRIAL: 20,
    ProjectType.RESIDENTIAL: 14,
    ProjectType.RENOVATION: 10,
    ProjectType.LAND: 8,
    ProjectType.OTHER: 5,
}

# ── Authority (15 pts) ────────────────────────────────────────────────────────
_AUTHORITY_SCORES: dict[DecisionAuthority, int] = {
    DecisionAuthority.SOLE: 15,
    DecisionAuthority.JOINT: 10,
    DecisionAuthority.INFLUENCER: 5,
    DecisionAuthority.UNKNOWN: 0,
}


def _data_quality_score(lead: Lead) -> int:
    """0-10 pts based on completeness of provided data."""
    score = 0
    if lead.contact.email:
        score += 3
    if lead.contact.city:
        score += 2
    if lead.budget_amount is not None:
        score += 3
    if lead.notes and len(lead.notes) >= 20:
        score += 2
    return score


class RuleBasedScorer:
    """Stateless scorer. All methods are pure (no I/O, no side effects)."""

    def compute(self, lead: Lead) -> int:
        budget = _BUDGET_SCORES[lead.budget_range]
        timeline = _TIMELINE_SCORES[lead.timeline_urgency]
        project = _PROJECT_SCORES[lead.project_type]
        authority = _AUTHORITY_SCORES[lead.decision_authority]
        quality = _data_quality_score(lead)

        total = budget + timeline + project + authority + quality
        # Clamp to [0, 100] (defensive; normal max is 100)
        return max(0, min(100, total))

    def breakdown(self, lead: Lead) -> dict[str, int]:
        """Returns per-dimension scores for debugging / reasoning context."""
        return {
            "budget": _BUDGET_SCORES[lead.budget_range],
            "timeline": _TIMELINE_SCORES[lead.timeline_urgency],
            "project_type": _PROJECT_SCORES[lead.project_type],
            "authority": _AUTHORITY_SCORES[lead.decision_authority],
            "data_quality": _data_quality_score(lead),
            "total": self.compute(lead),
        }
