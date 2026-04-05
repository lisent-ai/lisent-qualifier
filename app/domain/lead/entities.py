from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import LeadSource, ProjectType, BudgetRange, DecisionAuthority, TimelineUrgency


@dataclass(frozen=True)
class ContactInfo:
    name: str
    phone: str
    email: str = ""
    city: str = ""


@dataclass(frozen=True)
class Lead:
    id: str                           # dedup key (webhook payload'dan gelir)
    source: LeadSource
    contact: ContactInfo
    project_type: ProjectType
    budget_range: BudgetRange
    decision_authority: DecisionAuthority
    timeline_urgency: TimelineUrgency
    budget_amount: int | None = None  # TL cinsinden, varsa
    notes: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict, compare=False)
    received_at: datetime = field(default_factory=datetime.utcnow, compare=False)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "source": str(self.source),
            "contact": {
                "name": self.contact.name,
                "phone": self.contact.phone,
                "email": self.contact.email,
                "city": self.contact.city,
            },
            "project_type": str(self.project_type),
            "budget_range": str(self.budget_range),
            "budget_amount": self.budget_amount,
            "decision_authority": str(self.decision_authority),
            "timeline_urgency": str(self.timeline_urgency),
            "notes": self.notes,
            "received_at": self.received_at.isoformat(),
        }
        # Include all extra form data so the LLM can see it
        extra = self.raw_payload.get("extra_data", {})
        if extra:
            result["form_data"] = extra
        # Include raw_payload for handoff enrichment
        if self.raw_payload:
            result["raw_payload"] = self.raw_payload
        return result
