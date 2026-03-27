from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SessionStage(StrEnum):
    CHAT = "CHAT"
    HANDOFF = "HANDOFF"
    PENDING_CHAT = "PENDING_CHAT"  # Groq circuit open, waiting for retry


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
    bant_json: dict[str, Any] | None = None
    reasoning_json: dict[str, Any] | None = None
    last_bant_at: float | None = None
    created_at: float = field(default_factory=lambda: datetime.utcnow().timestamp())
    fallback_url: str | None = None
