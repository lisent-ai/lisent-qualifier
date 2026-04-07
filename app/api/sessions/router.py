"""
Session management endpoints — Conversation Takeover (Human-in-the-Loop).

Allows admin to take over an active AI conversation:
1. POST /internal/sessions/{cid}/{sid}/takeover — pause AI, enter human mode
2. POST /internal/sessions/{cid}/{sid}/send-manual — send message as admin
3. POST /internal/sessions/{cid}/{sid}/resume-ai — hand back to AI
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from app.config import get_settings
from app.infrastructure.redis.client import get_redis
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.db.lead_repo import insert_activity
from app.infrastructure.crm.rest_client import lookup_greenapi_by_company
from app.infrastructure.greenapi.client import send_whatsapp_message
from app.domain.conversation.session import SessionStage, ChatMessage
import structlog

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/internal/sessions", tags=["sessions"])


def _check_key(x_api_key: str | None) -> None:
    key = get_settings().internal_api_key
    if not key:
        return
    if x_api_key != key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")


def _get_session_repo() -> SessionRepository:
    return SessionRepository(get_redis())


# ── Start AI ────────────────────────────────────────────────────────────────

class StartAIRequest(BaseModel):
    actor: str = "dashboard"


@router.post("/{company_id}/{session_id}/start-ai")
async def start_ai(
    company_id: str,
    session_id: str,
    body: StartAIRequest,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    """PENDING session'ı CHAT'e çevir ve WhatsApp greeting queue'ya push et."""
    _check_key(x_api_key)
    repo = _get_session_repo()
    session = await repo.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    if session.stage == SessionStage.HANDOFF:
        raise HTTPException(status_code=422, detail="session already handed off")
    if session.stage == SessionStage.CHAT:
        return {"ok": True, "message": "AI already active", "stage": "CHAT"}
    if session.stage == SessionStage.HUMAN_TAKEOVER:
        raise HTTPException(status_code=422, detail="session in human takeover, resume AI first")
    if session.stage != SessionStage.PENDING:
        raise HTTPException(status_code=422, detail=f"unexpected stage: {session.stage}")

    # Stage → CHAT (Redis)
    await repo.set_stage(session_id, SessionStage.CHAT)

    # Stage → CHAT (PostgreSQL)
    try:
        pool = get_db_pool()
        await pool.execute(
            "UPDATE qualifier_sessions SET stage = 'CHAT', updated_at = now() WHERE id = $1::uuid",
            session_id,
        )
    except Exception as exc:
        log.warning("start_ai_db_stage_update_failed", error=str(exc))

    # WhatsApp greeting queue'ya push et
    phone = session.lead_json.get("contact", {}).get("phone", "") or session.lead_json.get("phone", "")
    if phone and company_id:
        await repo.push_wa_greeting(company_id, {
            "session_id": session_id,
            "company_id": company_id,
            "phone": phone,
        })

    # Lead status → contacted + activity log
    try:
        pool = get_db_pool()
        lead_id = session.lead_json.get("id", "")
        if lead_id and company_id:
            row = await pool.fetchrow(
                "SELECT id, status FROM qualifier_leads WHERE company_id=$1 AND lead_id=$2",
                company_id, lead_id,
            )
            if row:
                lead_db_id = str(row["id"])
                old_status = row["status"]
                # new → contacted (AI sohbeti başlatıldı)
                if old_status == "new":
                    await pool.execute(
                        "UPDATE qualifier_leads SET status='contacted', updated_at=now() WHERE id=$1::uuid",
                        lead_db_id,
                    )
                    await insert_activity(
                        pool, company_id=company_id, lead_db_id=lead_db_id,
                        event_type="status_change", actor="system",
                        payload={"old_status": old_status, "new_status": "contacted", "reason": "AI started"},
                    )
                await insert_activity(
                    pool, company_id=company_id, lead_db_id=lead_db_id,
                    event_type="ai_started", actor=body.actor,
                    payload={"session_id": session_id},
                )
    except Exception as exc:
        log.warning("start_ai_activity_log_failed", error=str(exc))

    log.info("ai_started", session_id=session_id, actor=body.actor)
    return {"ok": True, "previous_stage": "PENDING", "new_stage": "CHAT"}


# ── Takeover ────────────────────────────────────────────────────────────────

class TakeoverRequest(BaseModel):
    actor: str = "dashboard"


@router.post("/{company_id}/{session_id}/takeover")
async def takeover_session(
    company_id: str,
    session_id: str,
    body: TakeoverRequest,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    repo = _get_session_repo()
    session = await repo.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    if session.stage == SessionStage.HANDOFF:
        raise HTTPException(status_code=422, detail="session already handed off")
    if session.stage == SessionStage.HUMAN_TAKEOVER:
        return {"ok": True, "message": "already in takeover mode", "stage": "HUMAN_TAKEOVER"}

    previous_stage = str(session.stage)
    await repo.set_stage(session_id, SessionStage.HUMAN_TAKEOVER)

    # Persist stage to database (so it survives Redis restart/TTL expiry)
    try:
        pool = get_db_pool()
        await pool.execute(
            "UPDATE qualifier_sessions SET stage = 'HUMAN_TAKEOVER', updated_at = now() WHERE id = $1::uuid",
            session_id,
        )
    except Exception as exc:
        log.warning("takeover_db_stage_update_failed", error=str(exc))

    # Store previous stage in Redis for resume
    redis = get_redis()
    await redis.set(f"takeover_prev_stage:{session_id}", previous_stage, ex=86400)

    # Activity log
    try:
        pool = get_db_pool()
        lead_id = session.lead_json.get("id", "")
        if lead_id and company_id:
            row = await pool.fetchrow(
                "SELECT id FROM qualifier_leads WHERE company_id=$1 AND lead_id=$2",
                company_id, lead_id,
            )
            if row:
                await insert_activity(
                    pool, company_id=company_id, lead_db_id=str(row["id"]),
                    event_type="takeover", actor=body.actor,
                    payload={"session_id": session_id, "previous_stage": previous_stage},
                )
    except Exception as exc:
        log.warning("takeover_activity_log_failed", error=str(exc))

    log.info("session_takeover", session_id=session_id, actor=body.actor)
    return {"ok": True, "previous_stage": previous_stage, "new_stage": "HUMAN_TAKEOVER"}


# ── Send Manual Message ─────────────────────────────────────────────────────

class ManualMessageRequest(BaseModel):
    message: str
    actor: str = "dashboard"


@router.post("/{company_id}/{session_id}/send-manual")
async def send_manual_message(
    company_id: str,
    session_id: str,
    body: ManualMessageRequest,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    repo = _get_session_repo()
    session = await repo.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    if session.stage != SessionStage.HUMAN_TAKEOVER:
        raise HTTPException(status_code=422, detail="session not in takeover mode")

    # Append message to session (role=assistant from lead's perspective)
    msg = ChatMessage(role="assistant", content=body.message)
    session.messages.append(msg)
    session.msg_count += 1
    await repo.save(session)

    # Try to send via WhatsApp if we have credentials
    whatsapp_sent = False
    if company_id:
        try:
            integration = await lookup_greenapi_by_company(company_id)
            if integration:
                phone = session.lead_json.get("phone", "")
                if phone:
                    chat_id = f"{phone.lstrip('+')}" + "@c.us"
                    await send_whatsapp_message(
                        id_instance=int(integration["id_instance"]),
                        api_token=integration["api_token_instance"],
                        chat_id=chat_id,
                        message=body.message,
                    )
                    whatsapp_sent = True
        except Exception as exc:
            log.warning("manual_whatsapp_send_failed", error=str(exc))

    log.info("manual_message_sent", session_id=session_id, actor=body.actor, whatsapp=whatsapp_sent)
    return {"ok": True, "whatsapp_sent": whatsapp_sent, "msg_count": session.msg_count}


# ── Resume AI ───────────────────────────────────────────────────────────────

class ResumeRequest(BaseModel):
    actor: str = "dashboard"


@router.post("/{company_id}/{session_id}/resume-ai")
async def resume_ai(
    company_id: str,
    session_id: str,
    body: ResumeRequest,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    repo = _get_session_repo()
    session = await repo.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    if session.stage != SessionStage.HUMAN_TAKEOVER:
        raise HTTPException(status_code=422, detail="session not in takeover mode")

    # Restore previous stage
    redis = get_redis()
    prev = await redis.get(f"takeover_prev_stage:{session_id}")
    new_stage = SessionStage.CHAT
    if prev:
        try:
            new_stage = SessionStage(prev)
        except ValueError:
            new_stage = SessionStage.CHAT
        await redis.delete(f"takeover_prev_stage:{session_id}")

    await repo.set_stage(session_id, new_stage)

    # Persist stage to database
    try:
        pool = get_db_pool()
        await pool.execute(
            "UPDATE qualifier_sessions SET stage = $1, updated_at = now() WHERE id = $2::uuid",
            str(new_stage), session_id,
        )
    except Exception as exc:
        log.warning("resume_ai_db_stage_update_failed", error=str(exc))

    log.info("session_ai_resumed", session_id=session_id, actor=body.actor, new_stage=str(new_stage))
    return {"ok": True, "new_stage": str(new_stage)}


# ── Get Session Info ────────────────────────────────────────────────────────

@router.get("/{company_id}/{session_id}")
async def get_session_info(
    company_id: str,
    session_id: str,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    repo = _get_session_repo()
    session = await repo.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    return {
        "session_id": session.session_id,
        "stage": str(session.stage),
        "score": session.score,
        "msg_count": session.msg_count,
        "company_id": session.company_id,
        "messages": [{"role": m.role, "content": m.content, "ts": m.ts} for m in session.messages],
    }
