"""
ConversationHandler:
  - Stores user message
  - Triggers BANT extraction every N messages
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
from app.application.scoring.bant_extractor import extract_bant_task
from app.application.conversation.commands import SendMessageCommand

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
        Returns session state so the router can decide to schedule BANT task.
        """
        session = await self._session_repo.get(cmd.session_id)
        if session is None:
            return {"error": "session_not_found"}
        if session.stage == SessionStage.HANDOFF:
            return {"error": "session_already_handed_off"}

        # Append user message
        msg = ChatMessage(role="user", content=cmd.content)
        session.messages.append(msg)

        msg_count = await self._session_repo.increment_msg_count(cmd.session_id)
        session.msg_count = msg_count
        await self._session_repo.save(session)

        settings = get_settings()
        should_extract = (msg_count % settings.bant_extract_every_n_messages == 0)

        return {
            "session_id": cmd.session_id,
            "msg_count": msg_count,
            "should_extract_bant": should_extract,
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

        system_prompt = build_chat_system_prompt(session.lead_json, session.bant_json)
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

    async def should_extract_bant(self, session_id: str, msg_count: int) -> bool:
        settings = get_settings()
        return msg_count % settings.bant_extract_every_n_messages == 0
