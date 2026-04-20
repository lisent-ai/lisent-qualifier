"""
ConversationHandler:
  - Stores user message
  - Triggers CHAMP extraction every N messages
  - Streams Groq response via SSE
"""
import re
import structlog
from typing import AsyncGenerator

from app.config import get_settings
from app.domain.conversation.session import ChatMessage, SessionStage
from app.domain.conversation.prompts import build_chat_system_prompt
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.infrastructure.llm.groq_client import stream_chat
from app.application.scoring.champ_extractor import extract_champ_task
from app.application.conversation.commands import SendMessageCommand
from app.domain.scoring.signals.handoff_triggers import check_instant_handoff, check_conversation_end
from app.domain.scoring.signals.message_analyzer import MessageAnalyzer
from app.infrastructure.crm.rest_client import (
    fetch_company_kb_documents,
    fetch_company_ai_config,
    fetch_company_webhook_data,
)

log = structlog.get_logger(__name__)


_OPEN_QUESTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "tr": (
        "?",
        "ne ",
        "nedir",
        "nasil",
        "nasıl",
        "hangi",
        "kac",
        "kaç",
        "var mi",
        "var mı",
        "olur mu",
        "mantikli",
        "mantıklı",
        "oner",
        "öner",
        "gonder",
        "gönder",
        "bilgi",
        "detay",
    ),
    "en": (
        "?",
        "what",
        "which",
        "how",
        "can you",
        "could you",
        "details",
        "info",
        "send",
    ),
}

_FOLLOWUP_OFFER_PATTERNS: dict[str, tuple[str, ...]] = {
    "tr": (
        "gorusme",
        "görüşme",
        "telefon",
        "video",
        "randevu",
        "arayalim",
        "arayalım",
        "konusalim",
        "konuşalım",
        "danisman",
        "danışman",
        "temsilci",
        "uzman",
    ),
    "en": (
        "call",
        "meeting",
        "video",
        "specialist",
        "consultant",
        "representative",
        "speak",
        "schedule",
    ),
}

_FOLLOWUP_ACCEPTANCE_PATTERNS: dict[str, tuple[str, ...]] = {
    "tr": (
        "olur",
        "tamam",
        "yarin",
        "yarın",
        "ogleden sonra",
        "öğleden sonra",
        "sabah",
        "uygun",
        "goruselim",
        "görüşelim",
        "konusalim",
        "konuşalım",
    ),
    "en": (
        "ok",
        "okay",
        "sounds good",
        "tomorrow",
        "afternoon",
        "morning",
        "works for me",
        "let's do it",
    ),
}


def _message_requires_reply_before_handoff(message: str, language: str) -> bool:
    text = re.sub(r"\s+", " ", (message or "").strip().lower())
    if not text:
        return False

    patterns = _OPEN_QUESTION_PATTERNS.get(language, _OPEN_QUESTION_PATTERNS["en"])
    if any(token in text for token in patterns):
        return True

    if language == "tr":
        return any(
            phrase in text
            for phrase in (
                "iki konu hakkinda",
                "iki konu hakkında",
                "bilgi ver",
                "bilgi verir misin",
                "yonlendir",
                "yönlendir",
            )
        )
    return False


def _find_previous_assistant_message(messages: list[dict] | None) -> str:
    if not messages:
        return ""
    for message in reversed(messages):
        if str(message.get("role", "")).lower() == "assistant" and message.get("content"):
            return str(message["content"]).lower()
    return ""


def _is_accepting_human_followup(message: str, previous_assistant: str, language: str) -> bool:
    current = re.sub(r"\s+", " ", (message or "").strip().lower())
    if not current or not previous_assistant:
        return False

    offer_tokens = _FOLLOWUP_OFFER_PATTERNS.get(language, _FOLLOWUP_OFFER_PATTERNS["en"])
    acceptance_tokens = _FOLLOWUP_ACCEPTANCE_PATTERNS.get(language, _FOLLOWUP_ACCEPTANCE_PATTERNS["en"])

    if not any(token in previous_assistant for token in offer_tokens):
        return False

    return any(token in current for token in acceptance_tokens)


class ConversationHandler:
    def __init__(
        self,
        session_repo: SessionRepository,
        score_repo: ScoreRepository,
    ) -> None:
        self._session_repo = session_repo
        self._score_repo = score_repo

    async def handle_message(
        self,
        cmd: SendMessageCommand,
    ) -> dict:
        """
        Store user message, increment count.
        Returns session state so the router can decide to schedule CHAMP task.
        """
        session = await self._session_repo.get(cmd.session_id)
        if session is None:
            return {"error": "session_not_found"}
        if session.stage == SessionStage.HANDOFF:
            return {"error": "session_already_handed_off"}

        if session.stage == SessionStage.HUMAN_TAKEOVER:
            # Store message but don't trigger AI response
            msg = ChatMessage(role="user", content=cmd.content)
            session.messages.append(msg)
            session.msg_count += 1
            await self._session_repo.save(session)
            return {
                "session_id": cmd.session_id,
                "msg_count": session.msg_count,
                "should_extract_champ": False,
                "stage": "HUMAN_TAKEOVER",
                "human_takeover": True,
            }

        # Append user message
        msg = ChatMessage(role="user", content=cmd.content)
        session.messages.append(msg)

        msg_count = await self._session_repo.increment_msg_count(cmd.session_id)
        session.msg_count = msg_count
        await self._session_repo.save(session)

        # Fetch company config (needed for Layer 1 language + Layer 3 max_messages)
        company_config = None
        if session.company_id:
            try:
                company_config = await fetch_company_ai_config(session.company_id)
            except Exception as exc:
                log.warning("company_config_fetch_failed", error=str(exc))

        # ── LAYER 1: Instant trigger check ──────────────────────────────
        language = (company_config or {}).get("primary_language", "tr")
        previous_assistant_message = _find_previous_assistant_message(
            [{"role": m.role, "content": m.content} for m in session.messages[:-1]]
        )
        instant_handoff, instant_reason = check_instant_handoff(cmd.content, language)
        if not instant_handoff and _is_accepting_human_followup(
            cmd.content,
            previous_assistant_message,
            language,
        ):
            instant_handoff = True
            instant_reason = "accepted_human_followup"
        if instant_handoff:
            log.info(
                "instant_handoff_triggered",
                session_id=cmd.session_id,
                msg_count=msg_count,
                reason=instant_reason,
            )
            return {
                "session_id": cmd.session_id,
                "msg_count": msg_count,
                "should_extract_champ": False,
                "should_instant_handoff": True,
                "instant_handoff_reason": instant_reason,
                "stage": str(session.stage),
            }

        # ── LAYER 1.5: Per-message LLM classification + signal analysis ─
        settings = get_settings()
        sector = (company_config or {}).get("industry_focus", "construction")

        msg_dicts = [
            {"role": m.role, "content": m.content, "ts": m.ts}
            for m in session.messages
        ]
        conversation_stage = "early" if msg_count <= 3 else ("mid" if msg_count <= 6 else "late")

        # LLM classification (Groq → Local LLM → None)
        llm_result = None
        try:
            from app.infrastructure.llm.message_classifier import classify_message
            llm_result = await classify_message(
                message=cmd.content,
                messages=msg_dicts,
                stage=conversation_stage,
            )
        except Exception as exc:
            log.debug("llm_classification_error", error=str(exc)[:80])

        analyzer = MessageAnalyzer()
        signal_result = analyzer.analyze(
            message=cmd.content,
            messages=msg_dicts,
            msg_count=msg_count,
            language=language,
            sector=sector,
            extract_every_n=settings.champ_extract_every_n_messages,
            min_message_length=settings.signal_trigger_min_message_length,
            llm_classification=llm_result,
        )

        log.info(
            "message_signal_analysis",
            session_id=cmd.session_id,
            msg_count=msg_count,
            intent=signal_result.intent,
            information_value=signal_result.information_value,
            trigger=signal_result.should_trigger_extraction,
            trigger_reason=signal_result.trigger_reason,
            source=signal_result.classification_source,
        )

        # ── LAYER 2: Smart extraction trigger ───────────────────────────
        if settings.smart_extraction_enabled:
            should_extract = signal_result.should_trigger_extraction
        else:
            # Fallback: original periodic extraction
            should_extract = (msg_count % settings.champ_extract_every_n_messages == 0)

        # ── LAYER 2.5: Conversation end detection (goodbye → force handoff) ─
        force_handoff_after = False
        conv_ending, conv_end_reason = check_conversation_end(cmd.content, language)
        if conv_ending and msg_count >= 2:
            should_extract = True
            force_handoff_after = True
            log.info(
                "conversation_end_detected",
                session_id=cmd.session_id,
                msg_count=msg_count,
                reason=conv_end_reason,
            )

        # ── LAYER 3: Max messages → force extraction (soft cap) ─────────
        if not force_handoff_after:
            max_msgs = (company_config or {}).get("max_messages_before_handoff", 10)
            if msg_count >= max_msgs:
                if not should_extract:
                    should_extract = True
                if _message_requires_reply_before_handoff(cmd.content, language):
                    log.info(
                        "max_messages_reached_but_open_question",
                        session_id=cmd.session_id,
                        msg_count=msg_count,
                        max_msgs=max_msgs,
                    )
                else:
                    force_handoff_after = True
                    log.info(
                        "max_messages_reached",
                        session_id=cmd.session_id,
                        msg_count=msg_count,
                        max_msgs=max_msgs,
                    )

        return {
            "session_id": cmd.session_id,
            "msg_count": msg_count,
            "should_extract_champ": should_extract,
            "should_instant_handoff": False,
            "force_handoff_after_extract": force_handoff_after,
            "stage": str(session.stage),
            "signal_analysis": signal_result.to_dict(),
        }

    async def stream_response(
        self,
        session_id: str,
    ) -> AsyncGenerator[str, None]:
        """
        Build Groq messages and yield tokens.
        Also saves assistant response to session after streaming.
        """
        session = await self._session_repo.get(session_id)
        if session is None:
            yield "[ERROR: session not found]"
            return

        # Fetch company AI config if company_id is available
        company_config = None
        if session.company_id:
            try:
                company_config = await fetch_company_ai_config(session.company_id)
            except Exception as exc:
                log.warning("company_ai_config_fetch_failed", error=str(exc))

        # Fetch KB documents and merge into config
        if session.company_id and company_config is not None:
            try:
                kb_docs = await fetch_company_kb_documents(session.company_id)
                if kb_docs:
                    combined = "\n\n---\n\n".join(
                        f"[{doc.get('file_name', 'unknown')}]\n{doc.get('content', '')}"
                        for doc in kb_docs
                        if doc.get("active", True) and doc.get("content")
                    )
                    if combined:
                        # Truncate at merge time to avoid sending huge content
                        company_config = {**company_config, "kb_documents_content": combined[:8000]}
            except Exception as exc:
                log.warning("kb_documents_fetch_failed", error=str(exc))

        # Faz 6 — similarity search the per-company RAG knowledge base for
        # the most recent user turn. Injected into kb_documents_content so
        # the prompt builder renders it alongside any pre-loaded KB content.
        if session.company_id and company_config is not None:
            try:
                recent_user_msg = next(
                    (m.content for m in reversed(session.messages) if m.role == "user"),
                    None,
                )
                if recent_user_msg:
                    from app.infrastructure.rag import repository as _kb_repo

                    hits = await _kb_repo.search_chunks(
                        session.company_id, recent_user_msg, top_k=3
                    )
                    if hits:
                        rag_block = "\n\n---\n\n".join(
                            f"[{c.title or c.doc_ref}]\n{c.content}" for c in hits
                        )
                        existing_kb = company_config.get("kb_documents_content", "")
                        separator = "\n\n---\n\n" if existing_kb else ""
                        company_config = {
                            **company_config,
                            "kb_documents_content": (existing_kb + separator + rag_block)[:8000],
                        }
            except Exception as exc:
                log.warning("rag_retrieval_failed", error=str(exc))

        # Fetch webhook data, transform to KB text, and merge into KB content
        if session.company_id and company_config is not None:
            try:
                from app.domain.knowledge.project_transformer import transform_webhook_entries
                webhook_data = await fetch_company_webhook_data(session.company_id)
                if webhook_data:
                    language = (company_config or {}).get("primary_language", "tr")
                    webhook_kb = transform_webhook_entries(webhook_data, language)
                    if webhook_kb:
                        existing_kb = company_config.get("kb_documents_content", "")
                        separator = "\n\n---\n\n" if existing_kb else ""
                        company_config = {
                            **company_config,
                            "kb_documents_content": (existing_kb + separator + webhook_kb)[:8000],
                        }
            except Exception as exc:
                log.warning("webhook_data_fetch_failed", error=str(exc))

        prompt_messages = [{"role": m.role, "content": m.content} for m in session.messages]
        system_prompt = build_chat_system_prompt(
            session.lead_json,
            session.champ_json,
            company_config,
            messages=prompt_messages,
        )
        messages = [{"role": "system", "content": system_prompt}]
        messages += prompt_messages

        collected = []
        try:
            async for token in stream_chat(messages, session_id):
                collected.append(token)
                yield token
        except Exception as exc:
            log.error("groq_stream_error", session_id=session_id, error=str(exc))
            yield f"[STREAM_ERROR: {exc}]"
            return

        # Persist assistant response
        assistant_content = "".join(collected)
        if assistant_content:
            session_fresh = await self._session_repo.get(session_id)
            if session_fresh:
                session_fresh.messages.append(
                    ChatMessage(role="assistant", content=assistant_content)
                )
                await self._session_repo.save(session_fresh)

    async def should_extract_champ(self, session_id: str, msg_count: int) -> bool:
        settings = get_settings()
        return msg_count % settings.champ_extract_every_n_messages == 0
