from dataclasses import dataclass


@dataclass
class ReasoningReport:
    summary: str
    score_explanation: str
    key_signals: list[str]
    recommended_approach: str
    potential_objections: list[str]
    priority: str  # "high" | "medium" | "low"

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "score_explanation": self.score_explanation,
            "key_signals": self.key_signals,
            "recommended_approach": self.recommended_approach,
            "potential_objections": self.potential_objections,
            "priority": self.priority,
        }
