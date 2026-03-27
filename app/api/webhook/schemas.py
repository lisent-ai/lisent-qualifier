"""
WebhookLeadPayload — coercion enabled (strict=False).
Handles string→int budget coercion from social media form submissions.
"""
from pydantic import BaseModel, ConfigDict, Field, field_validator
import uuid


class WebhookLeadPayload(BaseModel):
    model_config = ConfigDict(strict=False, extra="allow")

    # Identity — generated server-side if absent
    lead_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # Contact
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=5, max_length=30)
    email: str = ""
    city: str = ""

    # Lead classification
    source: str = "other"
    project_type: str = "other"
    budget_range: str = "unknown"
    decision_authority: str = "unknown"
    timeline_urgency: str = "unknown"

    # Optional numeric budget (TL) — coerced from string if needed
    budget_amount: int | None = None

    # Free text
    notes: str = ""

    @field_validator("budget_amount", mode="before")
    @classmethod
    def coerce_budget(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            # Remove common formatting chars: "1.500.000 TL" → 1500000
            cleaned = v.replace(".", "").replace(",", "").replace(" ", "").rstrip("TLtl₺")
            try:
                return int(cleaned)
            except ValueError:
                return None
        return v
