"""
General/default sector qualifier — no sector-specific bonuses.
"""
from __future__ import annotations

from typing import Any

from app.domain.scoring.qualifiers.base import SectorQualifier


class GeneralQualifier(SectorQualifier):
    def compute_bonus(self, qualifiers: dict[str, Any]) -> int:
        return 0

    def compute_confidence_boost(self, qualifiers: dict[str, Any]) -> dict[str, float]:
        return {}
