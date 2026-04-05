"""
QualificationJudgment — LLM-based holistic lead qualification.

Backward-compatible with CHAMPScore: to_champ_dict() produces a dict
that CHAMPScore.from_dict() can consume. Extra fields (holistic_score,
reasoning, etc.) are preserved in the dict but ignored by CHAMPScore.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.scoring.champ import CHAMPScore


@dataclass
class QualificationJudgment:
    # Per-dimension scores (0-25 each, same scale as CHAMP)
    challenges_score: int = 0
    authority_score: int = 0
    money_score: int = 0
    prioritization_score: int = 0

    # Per-dimension reasoning (evidence from conversation)
    challenges_reasoning: str = ""
    authority_reasoning: str = ""
    money_reasoning: str = ""
    prioritization_reasoning: str = ""

    # Per-dimension confidence (0.0-1.0)
    challenges_confidence: float = 0.0
    authority_confidence: float = 0.0
    money_confidence: float = 0.0
    prioritization_confidence: float = 0.0

    # Holistic assessment (LLM's overall judgment)
    holistic_score: int = 0          # 0-100
    holistic_reasoning: str = ""
    icp_fit_assessment: str = ""

    # Negative signals
    negative_signals: list[str] = field(default_factory=list)
    negative_penalty: int = 0        # <= 0
    negative_reasoning: str = ""

    # Sector qualifiers
    sector_qualifiers: dict[str, Any] = field(default_factory=dict)

    # Next question / gap routing
    missing_info: list[str] = field(default_factory=list)
    recommended_next_question: str = ""

    # Handoff decision (Layer 2 — judge decides if lead is ready for sales)
    handoff_ready: bool = False
    handoff_reason: str = ""

    # CoT thinking (stored for audit, not used downstream)
    thinking: str = ""

    # Metadata
    extraction_version: int = 0
    scoring_mode: str = "llm_judge"

    # ── Computed properties ──────────────────────────────────────────────

    @property
    def total(self) -> int:
        return (
            self.challenges_score
            + self.authority_score
            + self.money_score
            + self.prioritization_score
        )

    @property
    def avg_confidence(self) -> float:
        confs = [
            self.challenges_confidence,
            self.authority_confidence,
            self.money_confidence,
            self.prioritization_confidence,
        ]
        return sum(confs) / len(confs) if confs else 0.0

    def filled_dimensions(self) -> int:
        return sum(
            1
            for s in (
                self.challenges_score,
                self.authority_score,
                self.money_score,
                self.prioritization_score,
            )
            if s > 0
        )

    def biggest_gap(self) -> str:
        dims = {
            "challenges": self.challenges_score,
            "authority": self.authority_score,
            "money": self.money_score,
            "prioritization": self.prioritization_score,
        }
        return min(dims, key=dims.get)  # type: ignore[arg-type]

    # ── Monotonic merge ──────────────────────────────────────────────────

    def merge_monotonic(self, newer: QualificationJudgment) -> QualificationJudgment:
        """
        Merge with a newer judgment. Scores never decrease (monotonic).
        Same per-dimension logic as CHAMPScore, plus holistic_score max.
        """

        def _pick(old_s: int, old_c: float, new_s: int, new_c: float) -> tuple[int, float]:
            if new_c >= old_c and new_s >= old_s:
                return new_s, new_c
            if new_s > old_s:
                return new_s, max(old_c, new_c)
            return old_s, old_c

        cs, cc = _pick(
            self.challenges_score, self.challenges_confidence,
            newer.challenges_score, newer.challenges_confidence,
        )
        aus, ac = _pick(
            self.authority_score, self.authority_confidence,
            newer.authority_score, newer.authority_confidence,
        )
        ms, mc = _pick(
            self.money_score, self.money_confidence,
            newer.money_score, newer.money_confidence,
        )
        ps, pc = _pick(
            self.prioritization_score, self.prioritization_confidence,
            newer.prioritization_score, newer.prioritization_confidence,
        )

        # Negatives accumulate (union), penalty takes the more severe
        merged_neg = list(set(self.negative_signals) | set(newer.negative_signals))
        merged_penalty = min(self.negative_penalty, newer.negative_penalty)

        # Sector qualifiers: prefer non-null from newer
        merged_sq = {**self.sector_qualifiers}
        for k, v in newer.sector_qualifiers.items():
            if v is not None:
                merged_sq[k] = v

        return QualificationJudgment(
            challenges_score=cs,
            authority_score=aus,
            money_score=ms,
            prioritization_score=ps,
            challenges_reasoning=newer.challenges_reasoning or self.challenges_reasoning,
            authority_reasoning=newer.authority_reasoning or self.authority_reasoning,
            money_reasoning=newer.money_reasoning or self.money_reasoning,
            prioritization_reasoning=newer.prioritization_reasoning or self.prioritization_reasoning,
            challenges_confidence=cc,
            authority_confidence=ac,
            money_confidence=mc,
            prioritization_confidence=pc,
            holistic_score=max(self.holistic_score, newer.holistic_score),
            holistic_reasoning=newer.holistic_reasoning or self.holistic_reasoning,
            icp_fit_assessment=newer.icp_fit_assessment or self.icp_fit_assessment,
            negative_signals=merged_neg,
            negative_penalty=merged_penalty,
            negative_reasoning=newer.negative_reasoning or self.negative_reasoning,
            sector_qualifiers=merged_sq,
            missing_info=newer.missing_info or self.missing_info,
            recommended_next_question=newer.recommended_next_question or self.recommended_next_question,
            handoff_ready=self.handoff_ready or newer.handoff_ready,
            handoff_reason=newer.handoff_reason or self.handoff_reason,
            thinking=newer.thinking or self.thinking,
            extraction_version=max(self.extraction_version, newer.extraction_version),
            scoring_mode=newer.scoring_mode,
        )

    # ── Backward-compat conversions ──────────────────────────────────────

    def to_champ_score(self) -> CHAMPScore:
        """Convert to CHAMPScore for CompositeScorer compatibility."""
        return CHAMPScore(
            challenges_score=self.challenges_score,
            authority_score=self.authority_score,
            money_score=self.money_score,
            prioritization_score=self.prioritization_score,
            challenges_notes=self.challenges_reasoning,
            authority_notes=self.authority_reasoning,
            money_notes=self.money_reasoning,
            prioritization_notes=self.prioritization_reasoning,
            challenges_confidence=self.challenges_confidence,
            authority_confidence=self.authority_confidence,
            money_confidence=self.money_confidence,
            prioritization_confidence=self.prioritization_confidence,
            extraction_version=self.extraction_version,
        )

    def to_champ_dict(self) -> dict[str, Any]:
        """
        Produce a dict that is a superset of CHAMPScore.to_dict().
        CHAMPScore.from_dict() will ignore extra fields.
        Downstream consumers (handoff, CRM, gap routing) get richer data.
        """
        return {
            # CHAMPScore-compatible fields
            "challenges_score": self.challenges_score,
            "authority_score": self.authority_score,
            "money_score": self.money_score,
            "prioritization_score": self.prioritization_score,
            "total": self.total,
            "challenges_notes": self.challenges_reasoning,
            "authority_notes": self.authority_reasoning,
            "money_notes": self.money_reasoning,
            "prioritization_notes": self.prioritization_reasoning,
            "challenges_confidence": self.challenges_confidence,
            "authority_confidence": self.authority_confidence,
            "money_confidence": self.money_confidence,
            "prioritization_confidence": self.prioritization_confidence,
            "extraction_version": self.extraction_version,
            # Judge-specific fields (ignored by CHAMPScore.from_dict())
            "holistic_score": self.holistic_score,
            "holistic_reasoning": self.holistic_reasoning,
            "icp_fit_assessment": self.icp_fit_assessment,
            "negative_signals": self.negative_signals,
            "negative_penalty": self.negative_penalty,
            "negative_reasoning": self.negative_reasoning,
            "sector_qualifiers": self.sector_qualifiers,
            "missing_info": self.missing_info,
            "recommended_next_question": self.recommended_next_question,
            "handoff_ready": self.handoff_ready,
            "handoff_reason": self.handoff_reason,
            "scoring_mode": self.scoring_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QualificationJudgment:
        return cls(
            challenges_score=data.get("challenges_score", 0),
            authority_score=data.get("authority_score", 0),
            money_score=data.get("money_score", 0),
            prioritization_score=data.get("prioritization_score", 0),
            challenges_reasoning=data.get("challenges_notes", "") or data.get("challenges_reasoning", ""),
            authority_reasoning=data.get("authority_notes", "") or data.get("authority_reasoning", ""),
            money_reasoning=data.get("money_notes", "") or data.get("money_reasoning", ""),
            prioritization_reasoning=data.get("prioritization_notes", "") or data.get("prioritization_reasoning", ""),
            challenges_confidence=data.get("challenges_confidence", 0.0),
            authority_confidence=data.get("authority_confidence", 0.0),
            money_confidence=data.get("money_confidence", 0.0),
            prioritization_confidence=data.get("prioritization_confidence", 0.0),
            holistic_score=data.get("holistic_score", 0),
            holistic_reasoning=data.get("holistic_reasoning", ""),
            icp_fit_assessment=data.get("icp_fit_assessment", ""),
            negative_signals=data.get("negative_signals", []),
            negative_penalty=data.get("negative_penalty", 0),
            negative_reasoning=data.get("negative_reasoning", ""),
            sector_qualifiers=data.get("sector_qualifiers", {}),
            missing_info=data.get("missing_info", []),
            recommended_next_question=data.get("recommended_next_question", ""),
            handoff_ready=data.get("handoff_ready", False),
            handoff_reason=data.get("handoff_reason", ""),
            thinking=data.get("thinking", ""),
            extraction_version=data.get("extraction_version", 0),
            scoring_mode=data.get("scoring_mode", "llm_judge"),
        )

    @classmethod
    def from_judgment_result(
        cls,
        result: Any,
        extraction_version: int = 1,
    ) -> QualificationJudgment:
        """Factory from QualificationJudgmentResult Pydantic model."""
        sq = {}
        if hasattr(result, "sector_qualifiers") and result.sector_qualifiers:
            sq = result.sector_qualifiers.model_dump(exclude_none=True)

        return cls(
            challenges_score=result.challenges_score,
            authority_score=result.authority_score,
            money_score=result.money_score,
            prioritization_score=result.prioritization_score,
            challenges_reasoning=result.challenges_reasoning,
            authority_reasoning=result.authority_reasoning,
            money_reasoning=result.money_reasoning,
            prioritization_reasoning=result.prioritization_reasoning,
            challenges_confidence=result.challenges_confidence,
            authority_confidence=result.authority_confidence,
            money_confidence=result.money_confidence,
            prioritization_confidence=result.prioritization_confidence,
            holistic_score=result.holistic_score,
            holistic_reasoning=result.holistic_reasoning,
            icp_fit_assessment=result.icp_fit_assessment,
            negative_signals=list(result.negative_signals),
            negative_penalty=result.negative_penalty,
            negative_reasoning=result.negative_reasoning,
            sector_qualifiers=sq,
            missing_info=list(result.missing_info),
            recommended_next_question=result.recommended_next_question,
            handoff_ready=getattr(result, "handoff_ready", False),
            handoff_reason=getattr(result, "handoff_reason", ""),
            thinking=result.thinking,
            extraction_version=extraction_version,
            scoring_mode="llm_judge",
        )
