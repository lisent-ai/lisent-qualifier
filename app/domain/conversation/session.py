from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SessionStage(StrEnum):
    PENDING = "PENDING"            # Lead scored, waiting for user to start AI
    CHAT = "CHAT"
    HANDOFF = "HANDOFF"
    PENDING_CHAT = "PENDING_CHAT"  # Groq circuit open, waiting for retry
    HUMAN_TAKEOVER = "HUMAN_TAKEOVER"  # Admin has taken over the conversation


@dataclass
class ChatMessage:
    role: str   # "user" | "assistant"
    content: str
    ts: float = field(default_factory=lambda: datetime.utcnow().timestamp())


@dataclass
class ConversationSession:
    session_id: str
    lead_json: dict[str, Any]
    score: int
    stage: SessionStage = SessionStage.CHAT
    msg_count: int = 0
    messages: list[ChatMessage] = field(default_factory=list)
    champ_json: dict[str, Any] | None = None
    reasoning_json: dict[str, Any] | None = None
    last_champ_at: float | None = None
    created_at: float = field(default_factory=lambda: datetime.utcnow().timestamp())
    fallback_url: str | None = None
    company_id: str = ""
    # WhatsApp delivery credentials (populated for WhatsApp-origin sessions so
    # the handoff handler can deliver the closing message back to the user).
    wa_instance_id: str = ""
    wa_api_token: str = ""
    wa_chat_id: str = ""
