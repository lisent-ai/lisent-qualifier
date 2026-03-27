from typing import Annotated

from fastapi import Depends

from app.infrastructure.redis.client import get_redis
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.config import get_settings, Settings
from app.application.lead_intake.handler import ProcessWebhookLeadHandler
from app.application.conversation.handler import ConversationHandler
from app.application.qualification.handler import HandoffHandler


def _get_session_repo() -> SessionRepository:
    settings = get_settings()
    return SessionRepository(get_redis(), ttl=settings.session_ttl_seconds)


def _get_score_repo() -> ScoreRepository:
    return ScoreRepository(get_redis())


SessionRepoDep = Annotated[SessionRepository, Depends(_get_session_repo)]
ScoreRepoDep = Annotated[ScoreRepository, Depends(_get_score_repo)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_lead_intake_handler(
    session_repo: SessionRepoDep,
    score_repo: ScoreRepoDep,
) -> ProcessWebhookLeadHandler:
    return ProcessWebhookLeadHandler(session_repo, score_repo)


def get_conversation_handler(
    session_repo: SessionRepoDep,
    score_repo: ScoreRepoDep,
) -> ConversationHandler:
    return ConversationHandler(session_repo, score_repo)


def get_handoff_handler(
    session_repo: SessionRepoDep,
    score_repo: ScoreRepoDep,
) -> HandoffHandler:
    return HandoffHandler(session_repo, score_repo)
