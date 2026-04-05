"""
Score event log — immutable record of every score change.

Used for audit trail, analytics, and debugging.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScoreEvent:
    session_id: str
    timestamp: float
    trigger: str  # "webhook_intake" | "champ_extraction" | "engagement_update" | "negative_signal"
    old_score: int
    new_score: int
    dimensions: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    extraction_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "trigger": self.trigger,
            "old_score": self.old_score,
            "new_score": self.new_score,
            "dimensions": self.dimensions,
            "confidence": round(self.confidence, 3),
            "extraction_version": self.extraction_version,
        }

    @classmethod
    def create(
        cls,
        session_id: str,
        trigger: str,
        old_score: int,
        new_score: int,
        dimensions: dict[str, Any] | None = None,
        confidence: float = 0.0,
        extraction_version: int = 0,
    ) -> "ScoreEvent":
        return cls(
            session_id=session_id,
            timestamp=time.time(),
            trigger=trigger,
            old_score=old_score,
            new_score=new_score,
            dimensions=dimensions or {},
            confidence=confidence,
            extraction_version=extraction_version,
        )
