"""
Dynamic qualification thresholds.

Higher-value leads need less qualification (sales team wants them faster).
Lower-value leads need more qualification (avoid wasting sales time).
"""
from typing import Any

# Default fallback — kept for backward compat imports
HIGH_THRESHOLD = 80

# Aggressiveness → base threshold mapping
AGGRESSIVENESS_MAP = {
    "conservative": 85,
    "balanced": 75,
    "aggressive": 60,
}


def resolve_base_threshold(company_config: dict[str, Any] | None) -> int:
    """Resolve base threshold from handoff_aggressiveness or legacy qualification_threshold."""
    cfg = company_config or {}
    aggressiveness = cfg.get("handoff_aggressiveness")
    if aggressiveness and aggressiveness in AGGRESSIVENESS_MAP:
        return AGGRESSIVENESS_MAP[aggressiveness]
    return int(cfg.get("qualification_threshold", 75))


def compute_threshold(
    project_type: str = "",
    budget_range: str = "",
    company_config: dict[str, Any] | None = None,
) -> int:
    """
    Return the qualification threshold for a given lead profile.

    Uses handoff_aggressiveness (preferred) or qualification_threshold as base.
    Adjusts +-10 based on project type and budget range.
    """
    base = resolve_base_threshold(company_config)

    # High-value commercial/industrial: sales team wants them fast
    if project_type in ("commercial", "industrial") and budget_range in (
        "over_10m",
        "3m_10m",
    ):
        return max(55, base - 10)

    # Mid-value residential
    if project_type == "residential" and budget_range in ("1m_3m", "3m_10m"):
        return base

    # Low-value renovation / small budget: need more qualification
    if project_type == "renovation" and budget_range in ("under_500k", "500k_1m"):
        return min(90, base + 10)

    # Land-only leads: moderate qualification
    if project_type == "land":
        return min(85, base + 5)

    return base
