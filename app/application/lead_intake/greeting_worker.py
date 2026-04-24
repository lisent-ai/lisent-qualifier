"""
WhatsApp greeting worker — queue'dan sırayla job alıp WhatsApp greeting gönderir.

Her company için sıralı çalışır (aynı anda birden fazla mesaj göndermez).
"""
import asyncio

import structlog

from app.infrastructure.crm.rest_client import lookup_greenapi_by_company
from app.infrastructure.greenapi.client import send_whatsapp_message
from app.infrastructure.redis.score_repo import ScoreRepository
from app.infrastructure.redis.session_repo import SessionRepository

log = structlog.get_logger(__name__)


async def process_greeting_job(
    job: dict,
    session_repo: SessionRepository,
    score_repo: ScoreRepository,
) -> None:
    """Tek bir WhatsApp greeting job'ını işle.

    Job format: {"session_id": "...", "company_id": "...", "phone": "..."}
    """
    session_id = job["session_id"]
    company_id = job["company_id"]
    phone = job.get("phone", "")

    if not phone:
        log.warning("greeting_job_no_phone", session_id=session_id)
        return

    # 1. Session'ın hâlâ aktif olduğunu kontrol et
    session = await session_repo.get(session_id)
    if session is None:
        log.warning("greeting_job_session_gone", session_id=session_id)
        return

    # Stage CHAT olmalı (start-ai endpoint CHAT'e çevirmiş olmalı)
    from app.domain.conversation.session import SessionStage
    if session.stage not in (SessionStage.CHAT, SessionStage.PENDING):
        log.info("greeting_job_wrong_stage", session_id=session_id, stage=str(session.stage))
        return

    # 2. AI greeting üret
    from app.application.conversation.handler import ConversationHandler
    conv_handler = ConversationHandler(session_repo, score_repo)
    tokens: list[str] = []
    async for token in conv_handler.stream_response(session_id):
        if token.startswith("[ERROR") or token.startswith("[STREAM_ERROR"):
            log.error("greeting_stream_error", session_id=session_id, token=token)
            return
        tokens.append(token)

    greeting = "".join(tokens).strip()
    if not greeting:
        log.warning("greeting_empty", session_id=session_id)
        return

    # 3. GreenAPI credentials
    integration = await lookup_greenapi_by_company(company_id)
    if not integration:
        log.info("greeting_no_whatsapp", company_id=company_id, session_id=session_id)
        return

    id_instance = integration["id_instance"]
    api_token = integration["api_token_instance"]

    # Normalize phone → WhatsApp chatId
    clean_phone = phone.lstrip("+").replace(" ", "").replace("-", "")
    chat_id = f"{clean_phone}@c.us"

    # 4. WhatsApp'a gönder (split messaging: --- ile bölünmüşse 2 mesaj)
    parts = greeting.split("---", 1)
    parts = [p.strip() for p in parts if p.strip()]

    sent = await send_whatsapp_message(
        id_instance=int(id_instance),
        api_token=api_token,
        chat_id=chat_id,
        message=parts[0],
    )
    if sent and len(parts) == 2:
        await asyncio.sleep(2.5)
        await send_whatsapp_message(
            id_instance=int(id_instance),
            api_token=api_token,
            chat_id=chat_id,
            message=parts[1],
        )

    if sent:
        # 5. phone→session mapping
        redis = session_repo._r
        phone_key = f"phone_session:{company_id}:{clean_phone}"
        await redis.set(phone_key, session_id, ex=86400)  # 24h TTL
        log.info(
            "greeting_sent",
            session_id=session_id,
            phone=clean_phone,
            greeting_len=len(greeting),
        )


async def wa_greeting_worker(session_repo: SessionRepository, score_repo: ScoreRepository) -> None:
    """Background worker — tüm company queue'larından sırayla greeting job'ları işler.

    5 saniyede bir poll eder. Her job arasında 3 saniye bekler (rate limiting).
    """
    log.info("wa_greeting_worker_started")
    while True:
        try:
            company_ids = await session_repo.list_wa_greeting_queues()
            processed_any = False

            for company_id in company_ids:
                job = await session_repo.pop_wa_greeting(company_id)
                if job is None:
                    continue

                processed_any = True
                try:
                    await process_greeting_job(job, session_repo, score_repo)
                except Exception as exc:
                    log.error("greeting_job_failed", error=str(exc),
                              session_id=job.get("session_id"))

                # Rate limiting: mesajlar arası bekleme
                await asyncio.sleep(3)

            if not processed_any:
                await asyncio.sleep(5)

        except Exception as exc:
            log.error("wa_greeting_worker_error", error=str(exc))
            await asyncio.sleep(10)
