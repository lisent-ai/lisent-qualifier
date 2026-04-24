"""
Internal service-to-service event ingest endpoints.

These endpoints are NOT tenant-scoped; they're called by the sibling Lisent
CRM Go service (and potentially other internal producers) to hand an event
off to the qualifier's outbound webhook pipeline. Authentication is a
shared secret (`QUALIFIER_INTERNAL_SECRET`) verified via constant-time
compare in an `X-Internal-Auth` header.

Currently registered:
    POST /v1/internal/events/lead-stage-changed
        Fired by CRM whenever `leads.status` changes. Produces:
            - lead.stage_changed  (always)
            - lead.won            (when to_status == 'converted')
            - lead.lost           (when to_status == 'lost')
            - lead.qualified      (when to_status == 'qualified', kept for
                                    legacy parity with the existing name)

The target tenant is resolved via `tenants.source_ref == company_id`
(the CRM's own company UUID), mirroring the tenant seeding invariant set up
in migration 003.
"""

from __future__ import annotations

import hmac
import os
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.infrastructure.db.pool import get_db_pool
from app.interfaces.rest_public.di import get_event_port
from app.ports.event import EventPort, ScoreEvent

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal/events", tags=["v1-internal-events"])


class LeadStageChangedRequest(BaseModel):
    company_id: UUID = Field(..., description="CRM company UUID — maps to tenants.source_ref")
    lead_id: UUID = Field(..., description="CRM lead UUID")
    from_status: str | None = None
    to_status: str
    changed_by: str | None = None
    changed_at: datetime | None = None
    lead: dict[str, Any] = Field(
        default_factory=dict,
        description="Full CRM lead snapshot; included in 'full' payload mode",
    )


class LeadStageChangedResponse(BaseModel):
    accepted: bool
    tenant_id: UUID | None
    events_published: list[str]


async def _verify_internal_auth(
    x_internal_auth: Annotated[str | None, Header(alias="X-Internal-Auth")] = None,
) -> None:
    expected = os.getenv("QUALIFIER_INTERNAL_SECRET", "").strip()
    if not expected:
        # Fail closed: if operator forgot to set the secret, reject every
        # call rather than silently accepting unauthenticated traffic.
        log.error("internal_auth_missing_server_secret")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal auth not configured",
        )
    got = (x_internal_auth or "").strip()
    if not got or not hmac.compare_digest(got, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid internal auth",
        )


_STATUS_TO_DERIVED_EVENT: dict[str, str] = {
    "converted": "lead.won",
    "lost": "lead.lost",
    "qualified": "lead.qualified",
}


async def _resolve_tenant_id(company_id: UUID) -> UUID | None:
    pool = get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id FROM tenants
            WHERE source_type = 'lisent_crm' AND source_ref = $1::text
            """,
            str(company_id),
        )
    return row["id"] if row else None


@router.post(
    "/lead-stage-changed",
    response_model=LeadStageChangedResponse,
    dependencies=[Depends(_verify_internal_auth)],
)
async def ingest_lead_stage_changed(
    body: LeadStageChangedRequest,
    event_port: Annotated[EventPort, Depends(get_event_port)],
) -> LeadStageChangedResponse:
    tenant_id = await _resolve_tenant_id(body.company_id)
    if tenant_id is None:
        # No matching tenant — this is a legitimate miss for CRM companies
        # that haven't been onboarded as qualifier tenants yet. Return 202-ish
        # with empty events list so CRM doesn't retry forever.
        log.info(
            "internal_stage_change_no_tenant",
            company_id=str(body.company_id),
            lead_id=str(body.lead_id),
            to_status=body.to_status,
        )
        return LeadStageChangedResponse(
            accepted=True, tenant_id=None, events_published=[]
        )

    changed_at = body.changed_at or datetime.utcnow()
    base_payload: dict[str, Any] = {
        "from_stage": body.from_status,
        "to_stage": body.to_status,
        "changed_by": body.changed_by,
        "changed_at": changed_at.isoformat(),
        "lead": body.lead,
    }

    published: list[str] = []

    async def _publish(event_type: str) -> None:
        evt = ScoreEvent(
            tenant_id=tenant_id,
            lead_id=body.lead_id,
            session_id=None,
            event_type=event_type,
            score=int(body.lead.get("score") or 0) if isinstance(body.lead, dict) else 0,
            threshold=0,
            path="crm",
            payload=dict(base_payload),
            timestamp=changed_at,
        )
        await event_port.publish(evt)
        published.append(event_type)

    await _publish("lead.stage_changed")

    derived = _STATUS_TO_DERIVED_EVENT.get(body.to_status)
    if derived:
        await _publish(derived)

    log.info(
        "internal_stage_change_ingested",
        tenant_id=str(tenant_id),
        lead_id=str(body.lead_id),
        to_status=body.to_status,
        events_published=published,
    )

    return LeadStageChangedResponse(
        accepted=True, tenant_id=tenant_id, events_published=published
    )
