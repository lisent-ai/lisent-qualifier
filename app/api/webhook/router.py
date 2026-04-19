import uuid

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import get_lead_intake_handler, SessionRepoDep, ScoreRepoDep
from app.application.lead_intake.commands import ProcessWebhookLeadCommand
from app.infrastructure.crm.rest_client import lookup_company_by_qualifier_token
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.db.lead_repo import upsert_lead
from app.infrastructure.llm.field_mapper import map_fields
import structlog

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
    # ── Body size check + JSON parse ────────────────────────────────────────
    body = await request.body()
    if len(body) > _MAX_BODY_SIZE:
        return JSONResponse({"error": "payload too large"}, status_code=413)

    import json as _json
    try:
        raw_payload: dict = _json.loads(body)
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    if not isinstance(raw_payload, dict):
        return JSONResponse({"error": "payload must be a JSON object"}, status_code=400)

    # ── Token → company_id + fallback_url ─────────────���─────────────────────
    company_info = await lookup_company_by_qualifier_token(company_token)
    if not company_info:
        log.warning("qualifier_token_invalid", token=company_token[-8:])
        return JSONResponse({"error": "invalid token"}, status_code=401)

    company_id = str(company_info["company_id"])
    fallback_url: str | None = company_info.get("fallback_url")

    # ── Field mapping (heuristic + LLM fallback) ───────────────────────────
    mapping = await map_fields(raw_payload)

    # ── lead_id: external_id > payload lead_id > auto UUID ──────────────────
    lead_id = mapping.external_id or raw_payload.get("lead_id") or str(uuid.uuid4())

    # ── Build lead_data for handler ─────────��─────────────────────���─────────
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

    # ── AI akışını çalıştır ─────────────────────────────────────────────────
    handler = get_lead_intake_handler(session_repo, score_repo)
    cmd = ProcessWebhookLeadCommand(
        lead_data=lead_data,
        fallback_url=fallback_url,
        company_id=company_id,
    )
    result = await handler.handle(cmd)



    # ── PostgreSQL'e kaydet ────────────────────────────────────────────────
    result_status = result.get("status", "chat_path")

    # Duplicate from Redis idempotency — skip DB write entirely,
    # the lead already exists from the first webhook call.
    if result_status == "duplicate":
        log.info("duplicate_skip_db", lead_id=lead_id)
        return result

    path = result_status.replace("_path", "")
    # Fast path → lead direkt qualified, chat path → new (AI bekliyor)
    lead_status = "qualified" if result_status == "fast_path" else "new"

    try:
        pool = get_db_pool()
        db_id = await upsert_lead(
            pool,
            company_id=company_id,
            lead_id=lead_id,
            phone=mapping.phone,
            name=mapping.full_name,
            email=mapping.email,
            city=mapping.city,
            source=mapping.source,
            project_type=mapping.project_type,
            budget_range=mapping.budget_range,
            score=result.get("score", 0),
            path=path,
            extra_data=mapping.extra_fields,
            score_breakdown=result.get("score_breakdown"),
            raw_payload=raw_payload,
        )

        # Fast path → status direkt qualified olarak güncelle
        if lead_status == "qualified" and db_id:
            await pool.execute(
                "UPDATE qualifier_leads SET status='qualified', updated_at=now() WHERE id=$1::uuid",
                db_id,
            )

        # Chat path ise qualifier_sessions tablosuna da yaz
        session_id = result.get("session_id")
        if session_id and db_id:
            await _upsert_session(pool, db_id, company_id, session_id, result.get("score", 0))

    except Exception as exc:
        log.error("qualifier_db_write_failed", error=str(exc), lead_id=lead_id)

    return result


# Faz 6 removed the legacy POST /webhook/rag/{token} that shipped payloads
# into the CRM as opaque blobs. The new ingestion pipeline at
# app/api/rag/router.py chunks + indexes content locally for retrieval,
# with proper idempotency + rate limit + per-doc lifecycle.

async def _upsert_session(
    pool, lead_db_id: str, company_id: str, session_id: str, score: int,
) -> None:
    """Chat path lead'inin session'ını qualifier_sessions tablosuna yaz.

    Aynı lead_id için zaten session varsa yeni oluşturmaz (duplicate webhook koruması).
    """
    try:
        existing = await pool.fetchval(
            "SELECT id FROM qualifier_sessions WHERE lead_id = $1::uuid LIMIT 1",
            lead_db_id,
        )
        if existing:
            log.debug("session_already_exists", lead_db_id=lead_db_id, existing_session=str(existing))
            return

        await pool.execute(
            """
            INSERT INTO qualifier_sessions (id, lead_id, company_id, score, stage, messages)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'PENDING', '[]'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            session_id, lead_db_id, company_id, score,
        )
    except Exception as exc:
        log.warning("session_db_write_failed", error=str(exc), session_id=session_id)
