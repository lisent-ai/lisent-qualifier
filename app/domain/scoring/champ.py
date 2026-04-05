"""
CHAMPScore — Challenges, Authority, Money, Prioritization.

Replaces BANTScore with per-dimension confidence tracking
and extraction versioning for audit trail.
"""
from dataclasses import dataclass, field


@dataclass
class CHAMPScore:
    # Scores: 0-25 each, total max 100
    challenges_score: int = 0
    authority_score: int = 0
    money_score: int = 0
    prioritization_score: int = 0

    # Evidence notes extracted from conversation
    challenges_notes: str = ""
    authority_notes: str = ""
    money_notes: str = ""
    prioritization_notes: str = ""

    # Per-dimension confidence: 0.0-1.0
    challenges_confidence: float = 0.0
    authority_confidence: float = 0.0
    money_confidence: float = 0.0
    prioritization_confidence: float = 0.0

    # Monotonic version — increments on each extraction
    extraction_version: int = 0

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
        """Number of dimensions with score > 0."""
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
        """Returns the dimension name with the lowest score."""
        dims = {
            "challenges": self.challenges_score,
            "authority": self.authority_score,
            "money": self.money_score,
            "prioritization": self.prioritization_score,
        }
        return min(dims, key=dims.get)  # type: ignore[arg-type]

    def merge_monotonic(self, newer: "CHAMPScore") -> "CHAMPScore":
        """
        Merge a newer extraction, keeping the higher score per dimension
        when the newer confidence >= the older confidence.
        Scores never decrease (monotonic).
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

        return CHAMPScore(
            challenges_score=cs,
            authority_score=aus,
            money_score=ms,
            prioritization_score=ps,
            challenges_notes=newer.challenges_notes or self.challenges_notes,
            authority_notes=newer.authority_notes or self.authority_notes,
            money_notes=newer.money_notes or self.money_notes,
            prioritization_notes=newer.prioritization_notes or self.prioritization_notes,
            challenges_confidence=cc,
            authority_confidence=ac,
            money_confidence=mc,
            prioritization_confidence=pc,
            extraction_version=max(self.extraction_version, newer.extraction_version),
        )

    def to_dict(self) -> dict:
        return {
            "challenges_score": self.challenges_score,
            "authority_score": self.authority_score,
            "money_score": self.money_score,
            "prioritization_score": self.prioritization_score,
            "total": self.total,
            "challenges_notes": self.challenges_notes,
            "authority_notes": self.authority_notes,
            "money_notes": self.money_notes,
            "prioritization_notes": self.prioritization_notes,
            "challenges_confidence": self.challenges_confidence,
            "authority_confidence": self.authority_confidence,
            "money_confidence": self.money_confidence,
            "prioritization_confidence": self.prioritization_confidence,
            "extraction_version": self.extraction_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CHAMPScore":
        return cls(
            challenges_score=data.get("challenges_score", 0),
            authority_score=data.get("authority_score", 0),
            money_score=data.get("money_score", 0),
            prioritization_score=data.get("prioritization_score", 0),
            challenges_notes=data.get("challenges_notes", ""),
            authority_notes=data.get("authority_notes", ""),
            money_notes=data.get("money_notes", ""),
            prioritization_notes=data.get("prioritization_notes", ""),
            challenges_confidence=data.get("challenges_confidence", 0.0),
            authority_confidence=data.get("authority_confidence", 0.0),
            money_confidence=data.get("money_confidence", 0.0),
            prioritization_confidence=data.get("prioritization_confidence", 0.0),
            extraction_version=data.get("extraction_version", 0),
        )
