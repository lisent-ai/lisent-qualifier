"""
Unified closing-message generation + delivery.

Consolidates three previously-duplicated sites that all had to build a closing
prompt, run it through Groq, and (for WhatsApp sessions) deliver the generated
message to the user. The previous ad-hoc code in two of those sites was sending
the raw SYSTEM PROMPT to the user as a WhatsApp message — this helper exists
to make that class of bug impossible going forward.

Callers:
  - HandoffHandler (chat path; qualified leads)
  - WhatsAppMessageHandler._handle_instant_handoff
  - WhatsAppMessageHandler._handle_force_handoff
"""
from __future__ import annotations

from typing import Any

import structlog

from app.config import get_settings
from app.domain.conversation.prompts import build_handoff_closing_prompt
from app.domain.conversation.session import ConversationSession
from app.domain.qualification.cta_router import CTAType
from app.infrastructure.greenapi.client import send_whatsapp_message
from app.infrastructure.llm.groq_client import complete_chat

log = structlog.get_logger(__name__)


_FALLBACK_MESSAGES = {
    CTAType.CYPRUS_VISIT: (
        "Konuştuğumuz başlıkları netleştirdik. Redif sizi Kıbrıs'a gelip villayı "
        "yerinde görmeye davet ediyor; size uygun bir vakitte misafirimiz olun."
    ),
    CTAType.CALENDLY: (
        "Konuştuğumuz noktaları netleştirmek için Redif'le 30 dakikalık kısa "
        "bir online görüşme iyi olur: {meeting_url}"
    ),
    CTAType.NURTURE: (
        "Şu an araştırma aşamasında olduğunuzu anlıyorum. Aklınıza "
        "takılan olursa buradan yazabilirsiniz."
    ),
}


def resolve_calendly_url(company_config: dict[str, Any] | None) -> str:
    cfg = company_config or {}
    url = cfg.get("cta_calendly_url") or ""
    if url:
        return str(url).strip()
    return get_settings().default_calendly_url


def _fallback_message(cta_type: CTAType, meeting_url: str) -> str:
    template = _FALLBACK_MESSAGES.get(cta_type, _FALLBACK_MESSAGES[CTAType.CYPRUS_VISIT])
    try:
        return template.format(meeting_url=meeting_url)
    except (KeyError, IndexError):
        return template


async def generate_closing_message(
    *,
    session: ConversationSession | None,
    company_config: dict[str, Any] | None,
    cta_type: CTAType,
    meeting_url: str | None,
) -> str:
    """
    Build the closing-message PROMPT, run it through Groq, and return the
    user-facing message. Falls back to a safe hardcoded message on Groq failure
    so we never leak the system prompt to the user.
    """
    session_messages: list[dict[str, Any]] | None = None
    lead_json: dict[str, Any] | None = None
    if session is not None:
        session_messages = [
            {"role": m.role, "content": m.content}
            for m in (session.messages or [])
        ]
        lead_json = session.lead_json

    closing_prompt = build_handoff_closing_prompt(
        company_config=company_config,
        lead_json=lead_json,
        messages=session_messages,
        cta_type=cta_type.value,
        meeting_url=meeting_url,
    )

    try:
        closing_msg = await complete_chat(
            [{"role": "system", "content": closing_prompt}]
        )
        if closing_msg and closing_msg.strip():
            log.info(
                "closing_message_generated",
                cta_type=cta_type.value,
                preview=closing_msg[:80],
            )
            return closing_msg.strip()
    except Exception as exc:
        log.warning(
            "closing_message_groq_failed",
            cta_type=cta_type.value,
            error=str(exc),
        )

    # Groq failed or returned empty — use safe fallback.
    return _fallback_message(cta_type, meeting_url or "")


async def deliver_to_whatsapp(
    *,
    message: str,
    instance_id: str,
    api_token: str,
    chat_id: str,
) -> bool:
    """
    Send a pre-generated closing message to WhatsApp via GreenAPI.
    Returns True on success.
    """
    if not (instance_id and api_token and chat_id and message):
        log.warning(
            "closing_delivery_missing_credentials",
            has_instance=bool(instance_id),
            has_token=bool(api_token),
            has_chat=bool(chat_id),
            has_message=bool(message),
        )
        return False
    try:
        await send_whatsapp_message(
            id_instance=int(instance_id),
            api_token=api_token,
            chat_id=chat_id,
            message=message,
        )
        return True
    except Exception as exc:
        log.warning("closing_delivery_failed", error=str(exc))
        return False


async def generate_and_deliver(
    *,
    session: ConversationSession | None,
    company_config: dict[str, Any] | None,
    cta_type: CTAType,
    meeting_url: str | None,
    wa_credentials: tuple[str, str, str] | None = None,
) -> str:
    """
    End-to-end: generate the closing message and (if credentials are provided)
    deliver it to WhatsApp. Returns the generated message either way so callers
    can also persist it.
    """
    message = await generate_closing_message(
        session=session,
        company_config=company_config,
        cta_type=cta_type,
        meeting_url=meeting_url,
    )

    if wa_credentials is not None:
        instance_id, api_token, chat_id = wa_credentials
        await deliver_to_whatsapp(
            message=message,
            instance_id=instance_id,
            api_token=api_token,
            chat_id=chat_id,
        )

    return message
