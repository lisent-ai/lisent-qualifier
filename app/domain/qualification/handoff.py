from dataclasses import dataclass
from typing import Any


@dataclass
class HandoffPackage:
    lead: dict[str, Any]
    score: int
    score_breakdown: dict[str, int]
    reasoning_report: dict[str, Any] | None
    bant: dict[str, Any] | None
    session_id: str | None  # None for fast-path (score >= 80 directly)
    path: str  # "fast" | "chat"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead": self.lead,
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "reasoning_report": self.reasoning_report,
            "bant": self.bant,
            "session_id": self.session_id,
            "path": self.path,
        }
