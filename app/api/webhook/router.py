from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.webhook.schemas import WebhookLeadPayload
from app.api.deps import get_lead_intake_handler, SessionRepoDep, ScoreRepoDep
from app.application.lead_intake.commands import ProcessWebhookLeadCommand
from app.infrastructure.crm.rest_client import lookup_company_by_qualifier_token
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.db.lead_repo import upsert_lead
import structlog

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.post("/lead/{company_token}", status_code=status.HTTP_202_ACCEPTED)
async def receive_lead(
    company_token: str,
    payload: WebhookLeadPayload,
    session_repo: SessionRepoDep,
    score_repo: ScoreRepoDep,
) -> dict:
    # Token → company_id + fallback_url
    company_info = await lookup_company_by_qualifier_token(company_token)
    if not company_info:
        log.warning("qualifier_token_invalid", token=company_token[-8:])
        return JSONResponse({"error": "invalid token"}, status_code=401)

    company_id = str(company_info["company_id"])
    fallback_url: str | None = company_info.get("fallback_url")

    extra = payload.extra_data()

    # Mevcut AI akışını çalıştır
    handler = get_lead_intake_handler(session_repo, score_repo)
    cmd = ProcessWebhookLeadCommand(
        lead_data={"name": payload.name, "phone": payload.phone, "lead_id": payload.lead_id, "extra_data": extra},
        fallback_url=fallback_url,
    )
    result = await handler.handle(cmd)

    # PostgreSQL'e kaydet
    try:
        pool = get_db_pool()
        await upsert_lead(
            pool,
            company_id=company_id,
            lead_id=payload.lead_id,
            phone=payload.phone,
            name=payload.name,
            score=result.get("score", 0),
            path=result.get("status", "chat").replace("_path", ""),
            extra_data=extra,
        )
    except Exception as exc:
        log.error("qualifier_db_write_failed", error=str(exc), lead_id=payload.lead_id)

    return result
