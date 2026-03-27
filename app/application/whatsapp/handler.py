"""
WhatsAppMessageHandler — WhatsApp mesajlarını AI lead qualifier akışına yönlendirir.

Akış:
  1. idMessage dedup kontrolü (GreenAPI 24h retry'a karşı)
  2. CRM'den idInstance → company_id + api_token çek
  3. phone → Redis'te mevcut session var mı?
     YOK → CRM'de customer + lead oluştur → ai-lead-qualifier session aç
     VAR → session_id al
  4. Mesajı ConversationHandler'a ilet
  5. AI yanıtını topla (stream_response tüketimi)
  6. GreenAPI ile WhatsApp'a geri gönder

Redis key şeması:
  phone_session:{company_id}:{phone}  → session_id (24h TTL)
  whatsapp_dedup:{idMessage}          → "1"        (5dk TTL)
"""
import structlog
from typing import Optional

from app.config import get_settings
from app.infrastructure.crm.rest_client import (
    lookup_greenapi_integration,
    find_or_create_customer,
    create_lead,
)
from app.infrastructure.greenapi.client import send_whatsapp_message
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.application.lead_intake.handler import ProcessWebhookLeadHandler
from app.application.lead_intake.commands import ProcessWebhookLeadCommand
from app.application.conversation.handler import ConversationHandler
from app.application.conversation.commands import SendMessageCommand

log = structlog.get_logger(__name__)

_PHONE_SESSION_PREFIX = "phone_session:"
_WA_DEDUP_PREFIX = "whatsapp_dedup:"
_PHONE_SESSION_TTL = 86400   # 24 saat
_DEDUP_TTL = 300             # 5 dakika


class WhatsAppMessageHandler:
    def __init__(
        self,
        session_repo: SessionRepository,
        score_repo: ScoreRepository,
    ) -> None:
        self._session_repo = session_repo
        self._score_repo = score_repo

    async def handle(
        self,
        id_instance: str,
        id_message: str,
        phone: str,
        chat_id: str,
        sender_name: str,
        text: str,
        _recursion: bool = False,
    ) -> dict:
        redis = self._session_repo._r

        # 1. Duplikat koruması — GreenAPI başarısız sayılan mesajları 24 saat içinde tekrar gönderebilir
        dedup_key = f"{_WA_DEDUP_PREFIX}{id_message}"
        is_new = await redis.set(dedup_key, "1", ex=_DEDUP_TTL, nx=True)
        if not is_new:
            log.info("whatsapp_duplicate_ignored", id_message=id_message)
            return {"status": "duplicate"}

        # 2. CRM'den instance → şirket bilgisi
        integration = await lookup_greenapi_integration(id_instance)
        if not integration:
            log.warning("greenapi_instance_not_found", id_instance=id_instance)
            return {"status": "unknown_instance"}

        company_id: str = str(integration["company_id"])
        api_token: str = integration["api_token_instance"]

        # 3. Mevcut session var mı?
        phone_key = f"{_PHONE_SESSION_PREFIX}{company_id}:{phone}"
        raw_session_id = await redis.get(phone_key)

        if raw_session_id is None:
            session_id = await self._start_new_lead_session(
                company_id=company_id,
                phone=phone,
                sender_name=sender_name,
                text=text,
                phone_key=phone_key,
                api_token=api_token,
                id_instance=id_instance,
                chat_id=chat_id,
            )
        else:
            session_id = raw_session_id.decode() if isinstance(raw_session_id, bytes) else raw_session_id
            log.info("whatsapp_existing_session", session_id=session_id, phone=phone)

        if not session_id:
            return {"status": "session_creation_failed"}

        # 4. Mesajı session'a ilet
        conv_handler = ConversationHandler(self._session_repo, self._score_repo)
        cmd = SendMessageCommand(session_id=session_id, content=text)
        result = await conv_handler.handle_message(cmd)

        if "error" in result:
            if result["error"] == "session_not_found" and not _recursion:
                # Session süresi dolmuş (24h Redis TTL) → yeniden başlat
                log.info("whatsapp_session_expired", session_id=session_id, phone=phone)
                await redis.delete(phone_key)
                # dedup key'i sil ki yeniden başlatmada engel olmasın
                await redis.delete(dedup_key)
                return await self.handle(
                    id_instance, id_message, phone, chat_id, sender_name, text,
                    _recursion=True,
                )
            log.warning("whatsapp_session_error", error=result["error"], session_id=session_id)
            return {"status": "error", "detail": result["error"]}

        # 5. AI yanıtını topla
        ai_response = await self._collect_ai_response(session_id)

        # 6. GreenAPI ile WhatsApp'a geri gönder
        if ai_response:
            await send_whatsapp_message(
                id_instance=int(id_instance),
                api_token=api_token,
                chat_id=chat_id,
                message=ai_response,
            )
        else:
            log.warning("whatsapp_empty_ai_response", session_id=session_id)

        return {"status": "ok", "session_id": session_id}

    async def _start_new_lead_session(
        self,
        company_id: str,
        phone: str,
        sender_name: str,
        text: str,
        phone_key: str,
        api_token: str,
        id_instance: str,
        chat_id: str,
    ) -> Optional[str]:
        """
        Yeni bir WhatsApp lead'i için:
          1. CRM'de customer bul veya oluştur
          2. CRM'de lead oluştur
          3. ai-lead-qualifier session başlat
          4. phone → session_id Redis mapping'i kaydet
        """
        # CRM customer
        customer = await find_or_create_customer(
            company_id=company_id,
            phone=phone,
            name=sender_name or phone,
        )
        if not customer:
            log.error("whatsapp_customer_creation_failed", phone=phone)
            return None

        # CRM lead
        lead = await create_lead(
            company_id=company_id,
            customer_id=customer["id"],
            source="whatsapp",
            extra_data={"channel": "whatsapp", "first_message": text},
        )

        # ai-lead-qualifier session — lead_id varsa kullan, yoksa None (handler üretir)
        lead_id = lead["id"] if lead else None
        intake_handler = ProcessWebhookLeadHandler(self._session_repo, self._score_repo)
        cmd = ProcessWebhookLeadCommand(lead_data={
            "lead_id": lead_id,
            "name": sender_name or phone,
            "phone": phone,
            "source": "whatsapp",
            "notes": text,
        })
        intake_result = await intake_handler.handle(cmd)

        # Fast path (score >= 80): lead doğrudan CRM'e gönderildi
        if intake_result.get("status") == "fast_path":
            log.info("whatsapp_fast_path", phone=phone, score=intake_result.get("score"))
            # Kullanıcıya "ekibimiz sizi arayacak" mesajı gönder
            settings = get_settings()
            qualified_msg = settings.whatsapp_qualified_message
            if qualified_msg:
                await send_whatsapp_message(
                    id_instance=int(id_instance),
                    api_token=api_token,
                    chat_id=chat_id,
                    message=qualified_msg,
                )
            return None  # Session yok, akış bitti

        session_id = intake_result.get("session_id")
        if session_id:
            redis = self._session_repo._r
            await redis.set(phone_key, session_id, ex=_PHONE_SESSION_TTL)
            log.info("whatsapp_new_session_created", session_id=session_id, phone=phone)

        return session_id

    async def _collect_ai_response(self, session_id: str) -> str:
        """
        ConversationHandler.stream_response() generator'ını tüketir,
        tüm token'ları birleştirip tek string döner.
        """
        conv_handler = ConversationHandler(self._session_repo, self._score_repo)
        tokens: list[str] = []
        async for token in conv_handler.stream_response(session_id):
            if token.startswith("[ERROR") or token.startswith("[STREAM_ERROR"):
                log.error("whatsapp_ai_stream_error", session_id=session_id, token=token)
                return ""
            tokens.append(token)
        return "".join(tokens)
