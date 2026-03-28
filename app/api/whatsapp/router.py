"""
GreenAPI WhatsApp webhook endpoint.

Güvenlik: Her şirkete bağlantı kurulurken otomatik üretilen webhook token ile doğrulama.
  - X-Webhook-Token header veya ?token= query param kabul edilir.
  - Şirkete ait token CRM'den idInstance lookup ile alınır.
  - Şirkette token yoksa (eski kayıtlar) global WHATSAPP_WEBHOOK_TOKEN env'e fallback.

Her durumda HTTP 200 döner — GreenAPI 5xx alırsa 1 dakika aralıklarla
24 saate kadar tekrar gönderebilir, bu istenmeyen davranışı önler.
"""
import hmac
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from typing import Optional

from app.api.whatsapp.schemas import GreenAPIWebhookPayload
from app.api.deps import SessionRepoDep, ScoreRepoDep
from app.application.whatsapp.handler import WhatsAppMessageHandler
from app.infrastructure.crm.rest_client import lookup_greenapi_integration
from app.config import get_settings

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["whatsapp"])


def _verify_token(provided: Optional[str], company_token: Optional[str]) -> bool:
    """
    Sabit-zamanlı (timing-safe) token karşılaştırması.
    Önce company_token'a bakar; yoksa global WHATSAPP_WEBHOOK_TOKEN'a fallback.
    İkisi de set edilmemişse geçer (geliştirme ortamı).
    """
    expected = company_token or get_settings().whatsapp_webhook_token
    if not expected:
        log.warning("whatsapp_webhook_token_not_set")
        return True
    if not provided:
        return False
    return hmac.compare_digest(expected, provided)


@router.post("/whatsapp", status_code=status.HTTP_200_OK)
async def receive_whatsapp(
    request: Request,
    session_repo: SessionRepoDep,
    score_repo: ScoreRepoDep,
) -> JSONResponse:
    raw = await request.json()
    log.info("whatsapp_raw_payload", payload=raw)

    # 1. Payload parse
    try:
        payload = GreenAPIWebhookPayload(**raw)
    except Exception as exc:
        log.error("whatsapp_parse_error", error=str(exc), payload=raw)
        return JSONResponse({"status": "parse_error"}, status_code=200)

    # 2. idInstance → company lookup (token doğrulama için gerekli)
    id_instance = str(payload.instanceData.idInstance)
    integration = await lookup_greenapi_integration(id_instance)
    if not integration:
        log.warning("greenapi_instance_not_found", id_instance=id_instance)
        return JSONResponse({"status": "unknown_instance"}, status_code=200)

    # 3. Token doğrulama: company token öncelikli, fallback global env
    token = (
        request.headers.get("X-Webhook-Token")
        or request.query_params.get("token")
    )
    if not _verify_token(token, integration.get("webhook_url_token")):
        log.warning("whatsapp_webhook_unauthorized", id_instance=id_instance)
        # 200 dön: 401 dönersek GreenAPI retry yapar
        return JSONResponse({"status": "unauthorized"}, status_code=200)

    # Her gelen webhook'u logla — debug için
    log.info("whatsapp_webhook_received",
             type=payload.typeWebhook,
             instance=id_instance,
             id_message=payload.idMessage,
             chat_id=payload.senderData.chatId if payload.senderData else None,
             msg_type=payload.messageData.typeMessage if payload.messageData else None)

    # incomingMessageReceived dışındaki event türlerini sessizce yoksay
    if payload.typeWebhook != "incomingMessageReceived":
        log.info("whatsapp_ignored", reason="not_incoming", type=payload.typeWebhook)
        return JSONResponse({"status": "ignored", "type": payload.typeWebhook})

    # Grup mesajlarını yoksay (@g.us) — extract_phone None döner
    phone = payload.extract_phone()
    if not phone:
        log.info("whatsapp_ignored", reason="group_or_no_phone",
                 chat_id=payload.senderData.chatId if payload.senderData else None)
        return JSONResponse({"status": "ignored", "reason": "group_or_no_phone"})

    # Metin dışı mesajları yoksay (resim, ses, belge vb.)
    text = payload.extract_text()
    if not text:
        log.info("whatsapp_ignored", reason="non_text_message",
                 msg_type=payload.messageData.typeMessage if payload.messageData else None)
        return JSONResponse({"status": "ignored", "reason": "non_text_message"})

    # integration'ı handler'a geç — double lookup önlenir
    handler = WhatsAppMessageHandler(session_repo, score_repo)
    result = await handler.handle(
        id_instance=id_instance,
        id_message=payload.idMessage,
        phone=phone,
        chat_id=payload.senderData.chatId,
        sender_name=payload.senderData.senderName or "",
        text=text,
        integration=integration,
    )

    return JSONResponse(result)
