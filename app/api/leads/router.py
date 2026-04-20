import csv
import io

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import get_settings
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.db.lead_repo import (
    list_leads, get_lead_with_session, list_active_sessions,
    insert_activity, list_activities,
    update_lead_status_with_log,
    assign_lead,
    list_handoffs,
    bulk_update_status, bulk_assign,
    export_leads_csv,
)

router = APIRouter(prefix="/internal", tags=["internal"])


def _check_key(x_api_key: str | None) -> None:
    key = get_settings().internal_api_key
    if not key:
        return
    if x_api_key != key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")


# ── Leads CRUD ──────────────────────────────────────────────────────────────

@router.get("/leads/{company_id}")
async def get_leads(
    company_id: str,
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    filter_status: str | None = Query(default=None, alias="status"),
    score_min: int | None = Query(default=None, ge=0, le=100),
    score_max: int | None = Query(default=None, ge=0, le=100),
    source: str | None = Query(default=None),
    path: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_dir: str = Query(default="desc"),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
) -> dict:
    _check_key(x_api_key)
    try:
        pool = get_db_pool()
        return await list_leads(
            pool, company_id=company_id, limit=limit, offset=offset,
            status=filter_status, score_min=score_min, score_max=score_max,
            source=source, path=path, search=search,
            sort_by=sort_by, sort_dir=sort_dir,
            date_from=date_from, date_to=date_to,
        )
    except RuntimeError:
        return {"data": [], "total": 0, "limit": limit, "offset": offset}


@router.get("/leads/{company_id}/active-sessions")
async def get_active_sessions(
    company_id: str,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    try:
        pool = get_db_pool()
        sessions = await list_active_sessions(pool, company_id=company_id)
        return {"data": sessions}
    except RuntimeError:
        return {"data": []}


# ── Bulk Operations (must be before {lead_id} wildcard) ─────────────────────

class BulkRequest(BaseModel):
    lead_ids: list[str]
    action: str
    params: dict


@router.post("/leads/{company_id}/bulk")
async def bulk_action(
    company_id: str, body: BulkRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    pool = get_db_pool()
    if body.action == "status_change":
        result = await bulk_update_status(
            pool, company_id=company_id, lead_ids=body.lead_ids,
            new_status=body.params.get("status", ""), actor=body.params.get("actor", "dashboard"),
        )
        # Trigger handoff for each lead when bulk-qualified
        if body.params.get("status") == "qualified":
            from app.infrastructure.redis.client import get_redis
            from app.infrastructure.redis.session_repo import SessionRepository
            from app.infrastructure.redis.score_repo import ScoreRepository
            from app.application.qualification.handler import HandoffHandler
            session_repo = SessionRepository(get_redis())
            score_repo = ScoreRepository(get_redis())
            handler = HandoffHandler(session_repo, score_repo)
            for lid in body.lead_ids:
                lead = await get_lead_with_session(pool, company_id=company_id, lead_db_id=lid)
                sid = lead.get("session_id") if lead else None
                if sid:
                    background_tasks.add_task(handler.handle, sid)
        return result
    elif body.action == "assign":
        return await bulk_assign(
            pool, company_id=company_id, lead_ids=body.lead_ids,
            assigned_to=body.params.get("assigned_to", ""), actor=body.params.get("actor", "dashboard"),
        )
    else:
        raise HTTPException(status_code=422, detail=f"unknown action: {body.action}")


# ── CSV Export (must be before {lead_id} wildcard) ──────────────────────────

@router.get("/leads/{company_id}/export")
async def export_leads(
    company_id: str,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    filter_status: str | None = Query(default=None, alias="status"),
    score_min: int | None = Query(default=None, ge=0, le=100),
    score_max: int | None = Query(default=None, ge=0, le=100),
    source: str | None = Query(default=None),
    path: str | None = Query(default=None),
) -> StreamingResponse:
    _check_key(x_api_key)
    pool = get_db_pool()
    leads = await export_leads_csv(
        pool, company_id=company_id, status=filter_status,
        score_min=score_min, score_max=score_max, source=source, path=path,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "lead_id", "name", "phone", "score", "path", "status", "assigned_to",
                     "source", "project_type", "budget_range", "city", "email", "created_at"])
    for lead in leads:
        writer.writerow([
            lead["id"], lead["lead_id"], lead["name"], lead["phone"],
            lead["score"], lead["path"], lead["status"], lead.get("assigned_to", ""),
            lead.get("source", ""), lead.get("project_type", ""), lead.get("budget_range", ""),
            lead.get("city", ""), lead.get("email", ""), lead["created_at"],
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads_export.csv"},
    )


# ── Lead Detail (wildcard — must come AFTER fixed paths) ────────────────────

@router.get("/leads/{company_id}/{lead_id}")
async def get_lead_detail(
    company_id: str,
    lead_id: str,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    try:
        pool = get_db_pool()
        lead = await get_lead_with_session(pool, company_id=company_id, lead_db_id=lead_id)
        if lead is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lead not found")

        # Redis'ten güncel session verilerini merge et (mesajlar, stage, score)
        session_id = lead.get("session_id")
        if session_id:
            try:
                from app.infrastructure.redis.client import get_redis
                from app.infrastructure.redis.session_repo import SessionRepository
                repo = SessionRepository(get_redis())
                session = await repo.get(session_id)
                if session:
                    lead["messages"] = [
                        {"role": m.role, "content": m.content, "ts": m.ts}
                        for m in session.messages
                    ]
                    lead["stage"] = str(session.stage)
                    lead["session_score"] = session.score
                    if session.champ_json:
                        lead["champ_json"] = session.champ_json
                    if session.reasoning_json:
                        lead["reasoning_json"] = session.reasoning_json
            except Exception:
                pass  # Redis unavailable — PostgreSQL verileri kullanılır

        return lead
    except HTTPException:
        raise
    except RuntimeError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="db unavailable")


# ── Delete Lead ────────────────────────────────────────────────────────────

@router.delete("/leads/{company_id}/{lead_id}")
async def delete_lead(
    company_id: str,
    lead_id: str,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    try:
        pool = get_db_pool()
        result = await pool.execute(
            "DELETE FROM qualifier_leads WHERE company_id = $1 AND id = $2::uuid",
            company_id, lead_id,
        )
        if result == "DELETE 0":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lead not found")
        return {"deleted": True}
    except HTTPException:
        raise
    except RuntimeError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="db unavailable")


# ── Status Lifecycle ────────────────────────────────────────────────────────

class StatusUpdateRequest(BaseModel):
    status: str
    reason: str = ""
    actor: str = "dashboard"


@router.patch("/leads/{company_id}/{lead_id}/status")
async def change_lead_status(
    company_id: str,
    lead_id: str,
    body: StatusUpdateRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    try:
        pool = get_db_pool()
        result = await update_lead_status_with_log(
            pool, company_id=company_id, lead_db_id=lead_id,
            new_status=body.status, actor=body.actor, reason=body.reason,
        )

        # Trigger handoff when status changes to "qualified"
        if body.status == "qualified":
            lead = await get_lead_with_session(pool, company_id=company_id, lead_db_id=lead_id)
            session_id = lead.get("session_id") if lead else None
            if session_id:
                from app.infrastructure.redis.client import get_redis
                from app.infrastructure.redis.session_repo import SessionRepository
                from app.infrastructure.redis.score_repo import ScoreRepository
                from app.application.qualification.handler import HandoffHandler
                session_repo = SessionRepository(get_redis())
                score_repo = ScoreRepository(get_redis())
                handler = HandoffHandler(session_repo, score_repo)
                background_tasks.add_task(handler.handle, session_id)

        return {"ok": True, **result}
    except ValueError as exc:
        msg = str(exc)
        if msg == "lead_not_found":
            raise HTTPException(status_code=404, detail="lead not found")
        raise HTTPException(status_code=422, detail=msg)


# ── Manual Qualify Trigger ──────────────────────────────────────────────────

class StartQualifyRequest(BaseModel):
    actor: str = "dashboard"


@router.post("/leads/{company_id}/{lead_id}/start-qualify")
async def start_lead_qualify(
    company_id: str,
    lead_id: str,
    body: StartQualifyRequest,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    """Operator-triggered kick-off for a manual-mode lead. Flips the paired
    PENDING session to CHAT and queues the WhatsApp greeting — same end
    state the auto flow would have produced, just deferred to a button."""

    _check_key(x_api_key)

    pool = get_db_pool()
    lead = await get_lead_with_session(pool, company_id=company_id, lead_db_id=lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")

    session_id = lead.get("session_id")
    if not session_id:
        raise HTTPException(status_code=422, detail="lead has no session to start")

    from app.infrastructure.redis.client import get_redis
    from app.infrastructure.redis.session_repo import SessionRepository
    from app.domain.conversation.session import SessionStage

    repo = SessionRepository(get_redis())
    session = await repo.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found in redis")

    if session.stage == SessionStage.CHAT:
        return {"ok": True, "already_active": True, "stage": "CHAT"}
    if session.stage != SessionStage.PENDING:
        raise HTTPException(status_code=422, detail=f"unexpected stage: {session.stage}")

    await repo.set_stage(session_id, SessionStage.CHAT)

    try:
        await pool.execute(
            "UPDATE qualifier_sessions SET stage = 'CHAT', updated_at = now() WHERE id = $1::uuid",
            session_id,
        )
    except Exception:
        pass

    # Queue a WhatsApp greeting if the lead has a phone. Same shape the
    # auto flow uses — the greeting worker owns the actual send.
    phone = (
        session.lead_json.get("contact", {}).get("phone", "")
        or session.lead_json.get("phone", "")
    )
    if phone and company_id:
        await repo.push_wa_greeting(
            company_id,
            {
                "session_id": session_id,
                "company_id": company_id,
                "phone": phone,
                "lead_id": session.lead_json.get("id", ""),
                "crm_lead_id": session.crm_lead_id,
            },
        )

    # Flip CRM ai_status pending → chatting so the UI reflects the new state.
    if session.crm_lead_id:
        from app.application.crm_sync import try_update_ai_metadata

        await try_update_ai_metadata(
            session.crm_lead_id,
            status="chatting",
            idempotency_key=f"lead-manual-start-{session.crm_lead_id}",
        )

    try:
        await insert_activity(
            pool,
            company_id=company_id,
            lead_db_id=lead_id,
            event_type="ai_started",
            actor=body.actor,
            payload={"session_id": session_id, "trigger": "manual"},
        )
    except Exception:
        pass

    return {"ok": True, "session_id": session_id, "new_stage": "CHAT"}


# ── Activity Timeline ───────────────────────────────────────────────────────

@router.get("/leads/{company_id}/{lead_id}/activity")
async def get_lead_activity(
    company_id: str,
    lead_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    try:
        pool = get_db_pool()
        return await list_activities(
            pool, company_id=company_id, lead_db_id=lead_id,
            limit=limit, offset=offset, event_type=event_type,
        )
    except RuntimeError:
        return {"data": [], "total": 0}


# ── Assignment ──────────────────────────────────────────────────────────────

class AssignRequest(BaseModel):
    assigned_to: str
    assigned_by: str = "dashboard"


@router.patch("/leads/{company_id}/{lead_id}/assign")
async def assign(
    company_id: str,
    lead_id: str,
    body: AssignRequest,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    pool = get_db_pool()
    return await assign_lead(
        pool, company_id=company_id, lead_db_id=lead_id,
        assigned_to=body.assigned_to, assigned_by=body.assigned_by,
    )


# ── CRM Handoffs ───────────────────────────────────────────────────────────

@router.get("/handoffs/{company_id}")
async def get_handoffs(
    company_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    try:
        pool = get_db_pool()
        return await list_handoffs(pool, company_id=company_id, limit=limit, offset=offset)
    except RuntimeError:
        return {"data": [], "total": 0}
