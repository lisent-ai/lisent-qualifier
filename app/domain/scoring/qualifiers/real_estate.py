"""
Real-estate sector qualifier bonuses.

Scores investor-specific signals extracted from conversation:
property shortlist, financing readiness, on-site visit intent,
decision-partner alignment, and exit-strategy clarity.
"""
from __future__ import annotations

from typing import Any

from app.domain.scoring.qualifiers.base import SectorQualifier


class RealEstateQualifier(SectorQualifier):
    def compute_bonus(self, qualifiers: dict[str, Any]) -> int:
        bonus = 0

        # Concrete shortlist: investor named 2+ specific properties/projects
        shortlist = qualifiers.get("has_property_shortlist")
        if shortlist is True or (isinstance(shortlist, int) and shortlist >= 2):
            bonus += 3

        # Financing readiness (cash, approved mortgage, deposit ready)
        financing = (qualifiers.get("financing_ready") or "").lower()
        if financing in ("cash", "nakit", "approved", "ready", "hazır", "hazir"):
            bonus += 2
        elif financing in ("pending", "basvuruldu", "başvuruldu"):
            bonus += 1

        # On-site visit intent: stated desire to come to Cyprus / tour the property
        if qualifiers.get("visit_intent"):
            bonus += 2

        # Decision-partner alignment (spouse, co-investor on board)
        if qualifiers.get("decision_partner_aligned"):
            bonus += 2

        # Exit strategy clear (rental, flip, holiday let)
        if qualifiers.get("exit_strategy_clear"):
            bonus += 1

        return min(10, bonus)

    def compute_confidence_boost(self, qualifiers: dict[str, Any]) -> dict[str, float]:
        boosts: dict[str, float] = {}

        shortlist = qualifiers.get("has_property_shortlist")
        if shortlist is True or (isinstance(shortlist, int) and shortlist >= 2):
            boosts["challenges_confidence"] = boosts.get("challenges_confidence", 0) + 0.15

        financing = (qualifiers.get("financing_ready") or "").lower()
        if financing in ("cash", "nakit", "approved", "ready", "hazır", "hazir"):
            boosts["money_confidence"] = boosts.get("money_confidence", 0) + 0.20
        elif financing in ("pending", "basvuruldu", "başvuruldu"):
            boosts["money_confidence"] = boosts.get("money_confidence", 0) + 0.05

        if qualifiers.get("visit_intent"):
            boosts["prioritization_confidence"] = boosts.get("prioritization_confidence", 0) + 0.15

        if qualifiers.get("decision_partner_aligned"):
            boosts["authority_confidence"] = boosts.get("authority_confidence", 0) + 0.15

        if qualifiers.get("exit_strategy_clear"):
            boosts["challenges_confidence"] = boosts.get("challenges_confidence", 0) + 0.05

        return boosts
