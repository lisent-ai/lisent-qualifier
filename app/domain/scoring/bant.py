from dataclasses import dataclass


@dataclass
class BANTScore:
    budget_score: int = 0      # 0-25
    authority_score: int = 0   # 0-25
    need_score: int = 0        # 0-25
    timeline_score: int = 0    # 0-25

    # Extracted text from conversation
    budget_notes: str = ""
    authority_notes: str = ""
    need_notes: str = ""
    timeline_notes: str = ""

    @property
    def total(self) -> int:
        return self.budget_score + self.authority_score + self.need_score + self.timeline_score

    def to_dict(self) -> dict:
        return {
            "budget_score": self.budget_score,
            "authority_score": self.authority_score,
            "need_score": self.need_score,
            "timeline_score": self.timeline_score,
            "total": self.total,
            "budget_notes": self.budget_notes,
            "authority_notes": self.authority_notes,
            "need_notes": self.need_notes,
            "timeline_notes": self.timeline_notes,
        }
