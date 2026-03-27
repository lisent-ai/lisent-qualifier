from dataclasses import dataclass


@dataclass(frozen=True)
class StartSessionCommand:
    session_id: str


@dataclass(frozen=True)
class SendMessageCommand:
    session_id: str
    content: str
