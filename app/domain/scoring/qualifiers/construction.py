"""
Construction sector qualifier bonuses.

Scores extra points for construction-specific signals:
land ownership, permit status, architect engagement, budget source, etc.
"""
from __future__ import annotations

from typing import Any

from app.domain.scoring.qualifiers.base import SectorQualifier


class ConstructionQualifier(SectorQualifier):
    def compute_bonus(self, qualifiers: dict[str, Any]) -> int:
        bonus = 0

        # Land ownership: strongest construction-specific signal
        if qualifiers.get("has_land"):
            bonus += 3

        # Permit status (imar durumu)
        permit = (qualifiers.get("permit_status") or "").lower()
        if permit in ("imarli", "imarlı", "approved", "zoned"):
            bonus += 3
        elif permit in ("basvuruldu", "başvuruldu", "pending", "applied"):
            bonus += 1

        # Working with architect/engineer
        if qualifiers.get("has_architect"):
            bonus += 2

        # Competing bids: actively shopping = high intent
        if qualifiers.get("competing_bids"):
            bonus += 1

        # Budget source clarity
        source = (qualifiers.get("budget_source") or "").lower()
        if source in ("nakit", "cash"):
            bonus += 1

        return min(10, bonus)

    def compute_confidence_boost(self, qualifiers: dict[str, Any]) -> dict[str, float]:
        boosts: dict[str, float] = {}

        if qualifiers.get("has_land"):
            boosts["challenges_confidence"] = boosts.get("challenges_confidence", 0) + 0.15

        permit = (qualifiers.get("permit_status") or "").lower()
        if permit in ("imarli", "imarlı", "approved", "zoned"):
            boosts["challenges_confidence"] = boosts.get("challenges_confidence", 0) + 0.10

        source = (qualifiers.get("budget_source") or "").lower()
        if source in ("nakit", "cash"):
            boosts["money_confidence"] = boosts.get("money_confidence", 0) + 0.20

        if qualifiers.get("competing_bids"):
            boosts["prioritization_confidence"] = boosts.get("prioritization_confidence", 0) + 0.10

        if qualifiers.get("project_sqm") and qualifiers["project_sqm"] > 0:
            boosts["challenges_confidence"] = boosts.get("challenges_confidence", 0) + 0.05

        return boosts
