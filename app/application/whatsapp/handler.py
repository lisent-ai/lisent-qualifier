"""
WhatsAppMessageHandler — WhatsApp mesajlarını AI lead qualifier akışına yönlendirir.

Akış:
  1. idMessage dedup kontrolü (GreenAPI 24h retry'a karşı)
  2. CRM'den idInstance → company_id + api_token çek
  3. phone → Redis'te mevcut session var mı?
     YOK → CRM'de customer + lead oluştur → ai-lead-qualifier session aç
     VAR → session_id al
  4. Mesajı Redis buffer'a ekle (debounce)
  5. 4 saniye bekle — yeni mesaj gelmezse buffer'ı birleştir
  6. Birleşik mesajı ConversationHandler'a ilet
  7. AI yanıtını topla + CHAMP extraction background'da
  8. GreenAPI ile WhatsApp'a geri gönder

Redis key şeması:
  phone_session:{company_id}:{phone}  → session_id      (24h TTL)
  whatsapp_dedup:{idMessage}          → "1"             (5dk TTL)
  wa_buffer:{company_id}:{phone}      → LIST of texts   (5dk TTL)
  wa_debounce:{company_id}:{phone}    → timestamp       (10s TTL)
"""
import asyncio
import time
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
from app.application.scoring.champ_extractor import extract_champ_task

log = structlog.get_logger(__name__)

_PHONE_SESSION_PREFIX = "phone_session:"
_WA_DEDUP_PREFIX = "whatsapp_dedup:"
_WA_BUFFER_PREFIX = "wa_buffer:"
_WA_DEBOUNCE_PREFIX = "wa_debounce:"
_PHONE_SESSION_TTL = 86400   # 24 saat
_DEDUP_TTL = 300             # 5 dakika
_BUFFER_TTL = 300            # 5 dakika safety
_DEBOUNCE_SECONDS = 7        # Ardışık mesaj bekleme süresi


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
        integration: Optional[dict] = None,
    ) -> dict:
        redis = self._session_repo._r

        # ── 1. Duplikat koruması ─────────────────────────────────────────────
        dedup_key = f"{_WA_DEDUP_PREFIX}{id_message}"
        is_new = await redis.set(dedup_key, "1", ex=_DEDUP_TTL, nx=True)
        if not is_new:
            log.info("whatsapp_duplicate_ignored", id_message=id_message)
            return {"status": "duplicate"}

        # ── 2. CRM'den instance → şirket bilgisi ────────────────────────────
        if integration is None:
            integration = await lookup_greenapi_integration(id_instance)
        if not integration:
            log.warning("greenapi_instance_not_found", id_instance=id_instance)
            return {"status": "unknown_instance"}

        company_id: str = str(integration["company_id"])
        api_token: str = integration["api_token_instance"]

        # ── 3. Mevcut session var mı? ────────────────────────────────────────
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

        if not session_id:
            return {"status": "session_creation_failed"}

        # ── 4. Mesajı buffer'a ekle + debounce ──────────────────────────────
        buffer_key = f"{_WA_BUFFER_PREFIX}{company_id}:{phone}"
        debounce_key = f"{_WA_DEBOUNCE_PREFIX}{company_id}:{phone}"

        await redis.rpush(buffer_key, text)
        await redis.expire(buffer_key, _BUFFER_TTL)

        # Debounce timestamp — her mesajda güncellenir
        now = str(time.time())
        await redis.set(debounce_key, now, ex=10)

        # Background'da debounce bekle + işle
        asyncio.create_task(self._debounced_process(
            debounce_key=debounce_key,
            buffer_key=buffer_key,
            my_timestamp=now,
            session_id=session_id,
            company_id=company_id,
            phone=phone,
            chat_id=chat_id,
            id_instance=id_instance,
            api_token=api_token,
            is_recursion=_recursion,
            integration=integration,
            sender_name=sender_name,
        ))

        return {"status": "buffered", "session_id": session_id}

    async def _debounced_process(
        self,
        debounce_key: str,
        buffer_key: str,
        my_timestamp: str,
        session_id: str,
        company_id: str,
        phone: str,
        chat_id: str,
        id_instance: str,
        api_token: str,
        is_recursion: bool,
        integration: dict,
        sender_name: str,
    ) -> None:
        """
        Debounce süresini bekle. Eğer bu mesaj hala son mesajsa,
        tüm buffer'ı birleştirip işle.
        """
        await asyncio.sleep(_DEBOUNCE_SECONDS)

        redis = self._session_repo._r

        # Bu hala son mesaj mı? (başka mesaj geldiyse timestamp değişmiştir)
        current_ts = await redis.get(debounce_key)
        if current_ts:
            current_ts = current_ts.decode() if isinstance(current_ts, bytes) else current_ts
        if current_ts != my_timestamp:
            # Daha yeni mesaj geldi — o task halledecek
            return

        # ── Buffer'ı al ve temizle ───────────────────────────────────────────
        raw_messages = await redis.lrange(buffer_key, 0, -1)
        await redis.delete(buffer_key, debounce_key)

        if not raw_messages:
            return

        combined_text = "\n".join(
            m.decode() if isinstance(m, bytes) else m
            for m in raw_messages
        )

        msg_count = len(raw_messages)
        log.info(
            "whatsapp_buffer_flushed",
            session_id=session_id,
            phone=phone,
            buffered_messages=msg_count,
            combined_len=len(combined_text),
        )

        # ── Birleşik mesajı session'a ilet ───────────────────────────────────
        conv_handler = ConversationHandler(self._session_repo, self._score_repo)
        cmd = SendMessageCommand(session_id=session_id, content=combined_text)
        result = await conv_handler.handle_message(cmd)

        if "error" in result:
            if result["error"] == "session_not_found" and not is_recursion:
                log.info("whatsapp_session_expired_debounced", session_id=session_id)
                phone_key = f"{_PHONE_SESSION_PREFIX}{company_id}:{phone}"
                await redis.delete(phone_key)
                # Yeniden session başlat + mesajı tekrar gönder
                new_session_id = await self._start_new_lead_session(
                    company_id=company_id,
                    phone=phone,
                    sender_name=sender_name,
                    text=combined_text,
                    phone_key=phone_key,
                    api_token=api_token,
                    id_instance=id_instance,
                    chat_id=chat_id,
                )
                if not new_session_id:
                    return
                session_id = new_session_id
                cmd = SendMessageCommand(session_id=session_id, content=combined_text)
                result = await conv_handler.handle_message(cmd)
                if "error" in result:
                    log.warning("whatsapp_retry_failed", error=result["error"])
                    return

            elif result["error"] == "session_already_handed_off":
                log.info("whatsapp_post_handoff_message", session_id=session_id)
                return
            else:
                log.warning("whatsapp_session_error", error=result["error"], session_id=session_id)
                return

        # ── Human takeover — AI yanıtı üretme ───────────────────────────────
        if result.get("human_takeover"):
            log.info("whatsapp_human_takeover_active", session_id=session_id)
            return

        # ── Force handoff — max mesaj ────────────────────────────────────────
        if result.get("should_force_handoff"):
            await self._handle_force_handoff(
                session_id=session_id,
                company_id=company_id,
                id_instance=id_instance,
                api_token=api_token,
                chat_id=chat_id,
            )
            return

        # ── AI yanıtını topla + split messaging ────────────────────────────
        ai_response = await self._collect_ai_response(session_id)

        if ai_response:
            await self._send_split_message(
                ai_response=ai_response,
                id_instance=id_instance,
                api_token=api_token,
                chat_id=chat_id,
            )
        else:
            log.warning("whatsapp_empty_ai_response", session_id=session_id)

        # ── CHAMP extraction — background (cevabı bloklamaz) ─────────────────
        current_msg_count = result.get("msg_count", 0)
        if current_msg_count >= 2 and result.get("should_extract_champ"):
            asyncio.create_task(self._background_champ(session_id))

    async def _handle_force_handoff(
        self,
        session_id: str,
        company_id: str,
        id_instance: str,
        api_token: str,
        chat_id: str,
    ) -> None:
        """Max mesaj sayısına ulaşıldığında: son CHAMP + kapanış mesajı + handoff."""
        log.info("whatsapp_force_handoff", session_id=session_id)

        # Son CHAMP extraction
        try:
            await extract_champ_task(session_id, self._session_repo, self._score_repo)
        except Exception as exc:
            log.warning("whatsapp_final_champ_failed", error=str(exc))

        # Kapanış mesajı
        from app.domain.conversation.prompts import build_handoff_closing_prompt
        from app.infrastructure.crm.rest_client import fetch_company_ai_config
        try:
            company_config = await fetch_company_ai_config(company_id)
            closing_msg = build_handoff_closing_prompt(company_config)
        except Exception:
            closing_msg = (
                "Ilginiz icin tesekkur ederiz! "
                "Uzman ekibimiz sizi en kisa surede arayacak. Iyi gunler!"
            )

        await send_whatsapp_message(
            id_instance=int(id_instance),
            api_token=api_token,
            chat_id=chat_id,
            message=closing_msg,
        )

        # Handoff
        from app.application.qualification.handler import HandoffHandler
        handoff_handler = HandoffHandler(self._session_repo, self._score_repo)
        await handoff_handler.handle(session_id)

    async def _background_champ(self, session_id: str) -> None:
        """CHAMP extraction — background task, AI cevabını bloklamaz."""
        try:
            log.info("whatsapp_champ_extraction_bg_start", session_id=session_id)
            await extract_champ_task(session_id, self._session_repo, self._score_repo)
            log.info("whatsapp_champ_extraction_bg_done", session_id=session_id)
        except Exception as exc:
            log.warning("whatsapp_champ_extraction_bg_failed", error=str(exc), session_id=session_id)

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
        customer = await find_or_create_customer(
            company_id=company_id,
            phone=phone,
            name=sender_name or phone,
        )
        if not customer:
            log.error("whatsapp_customer_creation_failed", phone=phone)
            return None

        lead = await create_lead(
            company_id=company_id,
            customer_id=customer["id"],
            source="whatsapp",
            extra_data={"channel": "whatsapp", "first_message": text},
        )

        lead_id = lead["id"] if lead else None
        intake_handler = ProcessWebhookLeadHandler(self._session_repo, self._score_repo)
        cmd = ProcessWebhookLeadCommand(lead_data={
            "lead_id": lead_id,
            "name": sender_name or phone,
            "phone": phone,
            "source": "whatsapp",
            "notes": text,
        }, company_id=company_id)
        intake_result = await intake_handler.handle(cmd)

        if intake_result.get("status") == "fast_path":
            log.info("whatsapp_fast_path", phone=phone, score=intake_result.get("score"))
            settings = get_settings()
            qualified_msg = settings.whatsapp_qualified_message
            if qualified_msg:
                await send_whatsapp_message(
                    id_instance=int(id_instance),
                    api_token=api_token,
                    chat_id=chat_id,
                    message=qualified_msg,
                )
            return None

        session_id = intake_result.get("session_id")
        if session_id:
            redis = self._session_repo._r
            await redis.set(phone_key, session_id, ex=_PHONE_SESSION_TTL)
            log.info("whatsapp_new_session_created", session_id=session_id, phone=phone)

        return session_id

    async def _send_split_message(
        self,
        ai_response: str,
        id_instance: str,
        api_token: str,
        chat_id: str,
    ) -> None:
        """
        AI cevabını --- separator'ına göre böl.
        İlk parça: tepki/espri (hemen gönder)
        İkinci parça: asıl cevap/soru (2-3 saniye sonra gönder)
        Separator yoksa tek mesaj olarak gönder.
        """
        parts = ai_response.split("---", 1)
        parts = [p.strip() for p in parts if p.strip()]

        if len(parts) == 2:
            # Split messaging: tepki + asıl cevap
            await send_whatsapp_message(
                id_instance=int(id_instance),
                api_token=api_token,
                chat_id=chat_id,
                message=parts[0],
            )
            await asyncio.sleep(2.5)  # Doğal yazım gecikmesi
            await send_whatsapp_message(
                id_instance=int(id_instance),
                api_token=api_token,
                chat_id=chat_id,
                message=parts[1],
            )
        else:
            # Tek mesaj
            await send_whatsapp_message(
                id_instance=int(id_instance),
                api_token=api_token,
                chat_id=chat_id,
                message=ai_response.replace("---", "").strip(),
            )

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
