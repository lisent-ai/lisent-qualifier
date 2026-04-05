"""
Abstract base for sector-specific qualification bonuses.

Sector qualifiers add bonus points based on domain-specific
signals extracted from conversation (e.g., land ownership for construction).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SectorQualifier(ABC):
    """Computes a 0-10 bonus score from sector-specific qualifiers."""

    @abstractmethod
    def compute_bonus(self, qualifiers: dict[str, Any]) -> int:
        """
        Parameters
        ----------
        qualifiers : dict
            Sector-specific qualifiers extracted by LLM
            (e.g., has_land, permit_status, has_architect, etc.)

        Returns
        -------
        int : 0-10 bonus points
        """
        ...

    @abstractmethod
    def compute_confidence_boost(self, qualifiers: dict[str, Any]) -> dict[str, float]:
        """
        Returns per-dimension confidence boosts based on sector qualifiers.
        Example: has_land=True → {"challenges_confidence": +0.15}
        """
        ...
