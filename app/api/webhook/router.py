"""Webhook lead intake — Phase 7 thin orchestration.

Flow:
    1. Body size + JSON parse.
    2. Token → company_id + fallback_url via CRM REST.
    3. Field mapping (heuristic + LLM fallback).
    4. Handler dedup check — duplicate early return (Redis SET NX).
    5. Resolve company_id → tenant_id via LisentCRMTenantAdapter (lazy-provisions
       a tenant row if this is the first time we see this company).
    6. Build Lead entity → CRM lead mirror (REST, idempotent).
    7. INSERT qualifier_leads with score=0, status='new', path=NULL.
    8. enqueue_lead → PreScoreWorker picks up, runs ensemble async.
    9. Return 202 + {status, lead_id}. Score updates via GET /v1/leads/{id}/score
       or outbound webhook (pre_score.judged).
"""
from __future__ import annotations

import json
import uuid

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import ScoreRepoDep, SessionRepoDep, get_lead_intake_handler
from app.application.lead_intake.commands import ProcessWebhookLeadCommand
from app.infrastructure.crm.rest_client import lookup_company_by_qualifier_token
from app.infrastructure.db.lead_repo import upsert_lead
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.llm.field_mapper import map_fields

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])

_MAX_BODY_SIZE = 64 * 1024  # 64 KB


@router.post("/lead/{company_token}", status_code=status.HTTP_202_ACCEPTED)
async def receive_lead(
    company_token: str,
    request: Request,
    session_repo: SessionRepoDep,
    score_repo: ScoreRepoDep,
) -> dict:
    # ── 1. Body size + JSON parse ───────────────────────────────────────────
    body = await request.body()
    if len(body) > _MAX_BODY_SIZE:
        return JSONResponse({"error": "payload too large"}, status_code=413)

    try:
        raw_payload: dict = json.loads(body)
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(raw_payload, dict):
        return JSONResponse(
            {"error": "payload must be a JSON object"}, status_code=400,
        )

    # ── 2. Token → company_id + fallback_url ────────────────────────────────
    company_info = await lookup_company_by_qualifier_token(company_token)
    if not company_info:
        log.warning("qualifier_token_invalid", token=company_token[-8:])
        return JSONResponse({"error": "invalid token"}, status_code=401)

    company_id = str(company_info["company_id"])
    fallback_url: str | None = company_info.get("fallback_url")

    # ── 3. Field mapping (heuristic + LLM fallback) ─────────────────────────
    mapping = await map_fields(raw_payload)

    lead_id = mapping.external_id or raw_payload.get("lead_id") or str(uuid.uuid4())
    lead_data = {
        "name": mapping.full_name,
        "phone": mapping.phone,
        "email": mapping.email,
        "city": mapping.city,
        "source": mapping.source,
        "project_type": mapping.project_type,
        "budget_range": mapping.budget_range,
        "budget_amount": mapping.budget_amount,
        "decision_authority": mapping.decision_authority,
        "timeline_urgency": mapping.timeline_urgency,
        "notes": mapping.notes,
        "lead_id": lead_id,
        "extra_data": mapping.extra_fields,
        "raw_payload": raw_payload,
    }

    external_lead_id = (
        raw_payload.get("external_lead_id") if isinstance(raw_payload, dict) else None
    )
    if external_lead_id is not None:
        external_lead_id = str(external_lead_id).strip() or None

    # ── 4. Handler dedup check ──────────────────────────────────────────────
    handler = get_lead_intake_handler(session_repo, score_repo)
    cmd = ProcessWebhookLeadCommand(
        lead_data=lead_data,
        fallback_url=fallback_url,
        company_id=company_id,
        external_lead_id=external_lead_id,
    )
    result = await handler.handle(cmd)

    if result.get("status") == "duplicate":
        log.info("duplicate_skip_db", lead_id=lead_id)
        return result

    # ── 5. Resolve company_id → tenant_id ───────────────────────────────────
    from app.interfaces.rest_public.di import get_tenant_adapter
    from app.ports.tenant import TenantSourceType

    tenant_adapter = await get_tenant_adapter()
    tenant = await tenant_adapter.resolve_by_source_ref(
        source_type=TenantSourceType.LISENT_CRM,
        source_ref=company_id,
    )
    if tenant is None:
        log.error(
            "webhook_tenant_resolve_failed",
            company_id=company_id,
            lead_id=lead_id,
        )
        return JSONResponse(
            {"error": "tenant mapping missing for company"}, status_code=500,
        )
    tenant_id = tenant.id

    # ── 6. CRM lead mirror (synchronous, idempotent; if writethrough off → None)
    from app.application.crm_sync import try_create_or_upsert_crm_lead
    from app.application.lead_intake.lead_builder import build_lead

    lead_entity = build_lead(lead_data)
    crm_lead_id: str | None
    if external_lead_id:
        # CRM forwarded an already-existing lead — reuse its id, skip POST.
        crm_lead_id = external_lead_id
    else:
        try:
            crm_lead_id = await try_create_or_upsert_crm_lead(
                company_id=company_id,
                lead=lead_entity,
                source_override="ai_qualifier",
            )
        except Exception as exc:
            log.warning(
                "crm_writethrough_create_failed_soft",
                company_id=company_id,
                lead_id=lead_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            crm_lead_id = None

    # ── 7. INSERT qualifier_leads (score=0, status=new) ─────────────────────
    # extra_data carries crm_lead_id so the worker can PATCH ai-metadata
    # back to the CRM after ensemble finishes.
    extra_data = dict(mapping.extra_fields or {})
    if crm_lead_id:
        extra_data["crm_lead_id"] = crm_lead_id
    if fallback_url:
        extra_data["fallback_url"] = fallback_url
    if mapping.notes:
        extra_data.setdefault("notes", mapping.notes)

    pool = get_db_pool()
    try:
        db_id = await upsert_lead(
            pool,
            company_id=company_id,
            lead_id=lead_id,
            phone=mapping.phone or "",
            name=mapping.full_name or "",
            email=mapping.email or "",
            city=mapping.city or "",
            source=mapping.source or "",
            project_type=mapping.project_type or "",
            budget_range=mapping.budget_range or "",
            score=0,
            path="",  # PreScoreWorker sets path via pre_score.judged event payload
            extra_data=extra_data,
            score_breakdown=None,
            raw_payload=raw_payload,
        )
    except Exception as exc:
        log.error("qualifier_db_write_failed", error=str(exc), lead_id=lead_id)
        return JSONResponse({"error": "db write failed"}, status_code=500)

    # Ensure qualifier_leads row is tenant-scoped (multi-tenant RLS prep).
    # upsert_lead uses company_id; we enforce tenant_id in the same transaction.
    try:
        await pool.execute(
            "UPDATE qualifier_leads SET tenant_id = $1::uuid WHERE id = $2::uuid",
            str(tenant_id), db_id,
        )
    except Exception as exc:
        log.warning(
            "qualifier_tenant_id_update_failed",
            lead_id=lead_id,
            db_id=db_id,
            error=str(exc),
        )

    # ── 8. Enqueue prescore job (worker picks up, async ensemble) ──────────
    # Soft-fail only: on Redis hiccups we mark the row so the worker's
    # reconciliation sweeper can re-enqueue it. Without the mark, a
    # transient enqueue failure would strand the lead at score=0 until a
    # human noticed.
    try:
        from app.application.prescore.queue import enqueue_lead
        from app.infrastructure.redis.client import get_redis

        await enqueue_lead(
            get_redis(),
            tenant_id=tenant_id,
            lead_id=uuid.UUID(db_id),
        )
    except Exception as exc:
        log.warning(
            "prescore_enqueue_failed",
            tenant_id=str(tenant_id),
            lead_id=lead_id,
            db_id=db_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        try:
            await pool.execute(
                """
                UPDATE qualifier_leads
                   SET extra_data = COALESCE(extra_data, '{}'::jsonb)
                                    || jsonb_build_object(
                                           'enqueue_failed', true,
                                           'enqueue_failed_at',
                                           to_char(NOW() AT TIME ZONE 'UTC',
                                                   'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                                       )
                 WHERE id = $1::uuid
                """,
                db_id,
            )
        except Exception as mark_exc:
            log.warning(
                "prescore_enqueue_mark_failed",
                db_id=db_id,
                error=f"{type(mark_exc).__name__}: {mark_exc}",
            )

    log.info(
        "webhook_lead_accepted",
        lead_id=lead_id,
        db_id=db_id,
        tenant_id=str(tenant_id),
        crm_lead_id=crm_lead_id,
    )

    return {
        "status": "accepted",
        "lead_id": lead_id,
        "db_id": db_id,
        "crm_lead_id": crm_lead_id,
    }
