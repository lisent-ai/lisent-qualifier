"""
ConversationHandler:
  - Stores user message
  - Triggers CHAMP extraction every N messages
  - Streams Groq response via SSE
"""
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
from app.infrastructure.crm.rest_client import fetch_company_kb_documents, fetch_company_ai_config

log = structlog.get_logger(__name__)


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

        settings = get_settings()
        should_extract = (msg_count % settings.champ_extract_every_n_messages == 0)

        # Check max messages before handoff
        should_force_handoff = False
        if session.company_id:
            try:
                company_config = await fetch_company_ai_config(session.company_id)
                if company_config:
                    max_msgs = company_config.get("max_messages_before_handoff", 10)
                    if msg_count >= max_msgs:
                        should_force_handoff = True
                        log.info(
                            "max_messages_reached",
                            session_id=cmd.session_id,
                            msg_count=msg_count,
                            max_msgs=max_msgs,
                        )
            except Exception as exc:
                log.warning("max_msg_check_failed", error=str(exc))

        return {
            "session_id": cmd.session_id,
            "msg_count": msg_count,
            "should_extract_champ": should_extract,
            "should_force_handoff": should_force_handoff,
            "stage": str(session.stage),
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
                        company_config = {**company_config, "kb_documents_content": combined[:4000]}
            except Exception as exc:
                log.warning("kb_documents_fetch_failed", error=str(exc))

        system_prompt = build_chat_system_prompt(session.lead_json, session.champ_json, company_config)
        messages = [{"role": "system", "content": system_prompt}]
        messages += [{"role": m.role, "content": m.content} for m in session.messages]

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
