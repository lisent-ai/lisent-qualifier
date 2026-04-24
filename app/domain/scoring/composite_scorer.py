"""
CompositeScorer — weighted fusion of multiple scoring signals.

Replaces the naive max(rule_based, bant) with:
  final = fit*w1 + qualification*w2 + engagement*w3 + negative + seasonal

All methods are pure (no I/O).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.scoring.champ import CHAMPScore

# ── Default weights (configurable per company via ScoringVariant) ─────────────

DEFAULT_WEIGHTS = {
    "fit": 0.30,
    "qualification": 0.45,
    "engagement": 0.15,
    "sector_bonus": 0.10,
}


@dataclass(frozen=True)
class CompositeResult:
    """Immutable result of a composite scoring computation."""
    final_score: int
    fit_score: int
    qualification_score: int
    engagement_score: int
    negative_adjustment: int
    sector_bonus: int
    seasonal_modifier: int
    confidence_multiplier: float
    raw_weighted: float  # before confidence & clamping

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_score": self.final_score,
            "fit_score": self.fit_score,
            "qualification_score": self.qualification_score,
            "engagement_score": self.engagement_score,
            "negative_adjustment": self.negative_adjustment,
            "sector_bonus": self.sector_bonus,
            "seasonal_modifier": self.seasonal_modifier,
            "confidence_multiplier": round(self.confidence_multiplier, 3),
            "raw_weighted": round(self.raw_weighted, 2),
        }


@dataclass
class ScoringWeights:
    fit: float = 0.30
    qualification: float = 0.45
    engagement: float = 0.15
    sector_bonus: float = 0.10

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> ScoringWeights:
        if not config:
            return cls()
        weights = config.get("scoring_weights", {})
        return cls(
            fit=weights.get("fit", 0.30),
            qualification=weights.get("qualification", 0.45),
            engagement=weights.get("engagement", 0.15),
            sector_bonus=weights.get("sector_bonus", 0.10),
        )


class CompositeScorer:
    """Stateless scorer. All methods are pure (no I/O, no side effects)."""

    def __init__(self, weights: ScoringWeights | None = None) -> None:
        self._w = weights or ScoringWeights()

    def compute(
        self,
        fit_score: int,
        champ: CHAMPScore | None = None,
        engagement_score: int = 0,
        negative_adjustment: int = 0,
        sector_bonus: int = 0,
        seasonal_modifier: int = 0,
    ) -> CompositeResult:
        """
        Compute the final composite score.

        Parameters
        ----------
        fit_score : int (0-100)
            Rule-based score from form/webhook data.
        champ : CHAMPScore | None
            LLM-extracted CHAMP scores.
        engagement_score : int (0-100)
            Behavioral signals (response speed, message length, etc.).
        negative_adjustment : int (typically <= 0)
            Disqualification penalties.
        sector_bonus : int (0-10)
            Sector-specific qualifier bonus.
        seasonal_modifier : int (-5 to +5)
            Seasonal adjustment.
        """
        qual_score = champ.total if champ else 0

        # Weighted blend (each input normalized to 0-100 scale)
        raw = (
            fit_score * self._w.fit
            + qual_score * self._w.qualification
            + engagement_score * self._w.engagement
            + sector_bonus * self._w.sector_bonus
        )

        # Confidence multiplier: low-confidence extractions reduce impact
        conf_mult = self._confidence_multiplier(champ)

        # Apply confidence, asymmetric negatives (0.5x weight), seasonal
        adjusted = raw * conf_mult + negative_adjustment * 0.5 + seasonal_modifier

        # Clamp to [0, 100]
        final = max(0, min(100, int(round(adjusted))))

        return CompositeResult(
            final_score=final,
            fit_score=fit_score,
            qualification_score=qual_score,
            engagement_score=engagement_score,
            negative_adjustment=negative_adjustment,
            sector_bonus=sector_bonus,
            seasonal_modifier=seasonal_modifier,
            confidence_multiplier=conf_mult,
            raw_weighted=raw,
        )

    @staticmethod
    def _confidence_multiplier(champ: CHAMPScore | None) -> float:
        """
        Scale between 0.7 and 1.0 based on average CHAMP confidence.
        When no CHAMP data exists, return 1.0 (no penalty — only fit score used).
        """
        if champ is None or champ.extraction_version == 0:
            return 1.0

        avg = champ.avg_confidence
        # Linear mapping: confidence 0.0 → 0.7, confidence 1.0 → 1.0
        return 0.7 + 0.3 * avg

    @staticmethod
    def controlled_merge(
        old_score: int,
        new_result: CompositeResult,
        score_floor: int = 30,
        max_decrease: int = 10,
    ) -> int:
        """Allow controlled decrease with floor protection.

        - Score can increase without limit.
        - Score can decrease at most ``max_decrease`` per extraction.
        - Score never drops below ``score_floor``.
        """
        new = new_result.final_score
        if new >= old_score:
            return new
        # Allow gentle decrease, capped
        return max(new, old_score - max_decrease, score_floor)

    # Keep backward compat
    @staticmethod
    def monotonic_merge(
        old_score: int,
        new_result: CompositeResult,
    ) -> int:
        """Ensure final score never decreases (legacy)."""
        return max(old_score, new_result.final_score)
