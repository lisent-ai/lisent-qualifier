"""CRM write-through helpers for the AI Lead Qualifier.

Faz 2 of the integrations plan: the qualifier mirrors every lead and every
scoring update into the CRM's leads table. This module centralizes the
write-through logic so that intake, chat, CHAMP extraction and handoff can
share a single call pattern.

All functions are no-ops when ``settings.qualifier_crm_writethrough_enabled``
is False — the caller still sees a successful return value (None / empty
string / no-op) so the legacy path remains unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import structlog

from app.config import get_settings
from app.domain.lead.entities import Lead
from app.infrastructure.crm.rest_client import (
    create_or_upsert_lead,
    update_lead_ai_metadata,
)

log = structlog.get_logger(__name__)


def _writethrough_enabled() -> bool:
    return bool(get_settings().qualifier_crm_writethrough_enabled)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lead_to_crm_payload(lead: Lead, source_override: str | None) -> dict[str, Any]:
    """Map a qualifier Lead entity onto a CRM /leads POST payload.

    Fields unknown to the CRM schema end up in extra_data. Kept conservative —
    anything we're unsure about is stashed in extra_data rather than dropped.
    """
    lead_dict = lead.to_dict()
    extra: dict[str, Any] = dict(lead_dict.get("extra_data") or {})

    # Preserve enum values the CRM doesn't have a direct column for.
    for key in (
        "project_type",
        "budget_range",
        "budget_amount",
        "decision_authority",
        "timeline_urgency",
        "raw_payload",
    ):
        if key in lead_dict and lead_dict[key] is not None:
            extra.setdefault(key, lead_dict[key])

    contact = lead_dict.get("contact") or {}
    source = source_override or str(lead_dict.get("source") or "ai_qualifier")

    return {
        "name": contact.get("name", "") or "",
        "email": contact.get("email", "") or "",
        "phone": contact.get("phone", "") or "",
        "notes": lead_dict.get("notes", "") or "",
        "source": source,
        "status": "new",
        "extra_data": extra,
    }


async def try_create_or_upsert_crm_lead(
    company_id: str,
    lead: Lead,
    *,
    source_override: str | None = None,
) -> Optional[str]:
    """Mirror the qualifier lead into CRM and return the CRM lead_id.

    Idempotent via the qualifier's internal lead id (``lead.id``). Returns
    None when write-through is disabled OR the CRM call fails — callers must
    treat None as "no CRM mirror yet" and skip downstream ai-metadata calls
    to avoid PATCH-ing a non-existent row.
    """
    if not _writethrough_enabled() or not company_id or not lead.id:
        return None

    payload = _lead_to_crm_payload(lead, source_override)
    result = await create_or_upsert_lead(
        company_id=company_id,
        lead_data=payload,
        qualifier_external_ref=lead.id,
    )
    if not result:
        log.warning(
            "crm_writethrough_create_failed",
            company_id=company_id,
            external_ref=lead.id,
        )
        return None
    crm_lead_id = str(result.get("id") or "")
    if not crm_lead_id:
        log.warning(
            "crm_writethrough_create_no_id",
            company_id=company_id,
            external_ref=lead.id,
        )
        return None
    log.info(
        "crm_writethrough_lead_synced",
        company_id=company_id,
        external_ref=lead.id,
        crm_lead_id=crm_lead_id,
    )
    return crm_lead_id


async def try_update_ai_metadata(
    crm_lead_id: str,
    *,
    score: int | None = None,
    status: str | None = None,
    session_id: str | None = None,
    champ: Mapping[str, Any] | None = None,
    reasoning: Mapping[str, Any] | None = None,
    score_breakdown: Mapping[str, Any] | None = None,
    path: str | None = None,
    last_scored_at: str | None = None,
    idempotency_key: str | None = None,
    actor: str = "ai_qualifier",
) -> bool:
    """PATCH the CRM lead's AI metadata.

    Returns True on success, False on (silent) failure. Never raises: CRM
    unavailability must not crash a chat / scoring cycle.
    """
    if not _writethrough_enabled() or not crm_lead_id:
        return False

    payload: dict[str, Any] = {"actor": actor}
    if score is not None:
        payload["ai_score"] = int(score)
    if status is not None:
        payload["ai_status"] = status
    if session_id is not None:
        payload["ai_session_id"] = session_id
    if champ is not None:
        payload["ai_champ"] = dict(champ)
    if reasoning is not None:
        payload["ai_reasoning"] = dict(reasoning)
    if score_breakdown is not None:
        payload["ai_score_breakdown"] = dict(score_breakdown)
    if path is not None:
        payload["ai_path"] = path
    payload["ai_last_scored_at"] = last_scored_at or _now_iso()

    result = await update_lead_ai_metadata(
        lead_id=crm_lead_id,
        ai_payload=payload,
        idempotency_key=idempotency_key,
    )
    if result is None:
        log.warning(
            "crm_writethrough_ai_metadata_failed",
            crm_lead_id=crm_lead_id,
            idempotency_key=idempotency_key,
        )
        return False
    return True
