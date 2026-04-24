"""Lead entity builder — webhook payload dict → domain Lead.

Extracted from the old `ProcessWebhookLeadHandler._build_lead` so the router
can build a Lead for the CRM write-through (`try_create_or_upsert_crm_lead`)
without the handler being involved.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.domain.lead.entities import ContactInfo, Lead
from app.domain.lead.enums import (
    BudgetRange,
    DecisionAuthority,
    LeadSource,
    ProjectType,
    TimelineUrgency,
)


def _safe(enum_cls, value, default):
    """Best-effort enum coerce. Unknown / junk values fall back to default."""
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return default


def build_lead(data: dict[str, Any]) -> Lead:
    """Map webhook payload (flat dict from field_mapper) to domain Lead."""
    extra = data.get("extra_data", {})

    def get(key: str, default: Any = "") -> Any:
        return data.get(key) or extra.get(key) or default

    contact = ContactInfo(
        name=get("name"),
        phone=get("phone"),
        email=get("email"),
        city=get("city"),
    )
    return Lead(
        id=get("lead_id") or str(uuid.uuid4()),
        source=_safe(LeadSource, get("source"), LeadSource.OTHER),
        contact=contact,
        project_type=_safe(ProjectType, get("project_type"), ProjectType.OTHER),
        budget_range=_safe(BudgetRange, get("budget_range"), BudgetRange.UNKNOWN),
        decision_authority=_safe(
            DecisionAuthority, get("decision_authority"), DecisionAuthority.UNKNOWN,
        ),
        timeline_urgency=_safe(
            TimelineUrgency, get("timeline_urgency"), TimelineUrgency.UNKNOWN,
        ),
        budget_amount=get("budget_amount", None),
        notes=get("notes"),
        raw_payload=data.get("raw_payload", data),
    )
