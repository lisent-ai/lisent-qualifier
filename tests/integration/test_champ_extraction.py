"""
Integration test: CHAMP score triggers handoff when threshold crossed.
"""
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, patch

from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.domain.conversation.session import ConversationSession, SessionStage, ChatMessage
from app.application.scoring.champ_extractor import extract_champ_task
from app.infrastructure.llm.schemas import CHAMPExtractionResult


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def session_repo(fake_redis):
    return SessionRepository(fake_redis, ttl=3600)


@pytest.fixture
def score_repo(fake_redis):
    return ScoreRepository(fake_redis)


@pytest.mark.asyncio
async def test_high_champ_score_triggers_handoff(session_repo, score_repo, fake_redis):
    """When CHAMP extraction yields high composite score, HandoffHandler should be called."""
    session = ConversationSession(
        session_id="sess-handoff",
        lead_json={"contact": {"name": "Test"}, "project_type": "commercial", "budget_range": "over_10m"},
        score=70,
        stage=SessionStage.CHAT,
        messages=[
            ChatMessage(role="user", content="10 milyon bütçem var ve hemen başlamak istiyorum"),
            ChatMessage(role="assistant", content="Harika, daha fazla bilgi verebilir misiniz?"),
            ChatMessage(role="user", content="Evet, karar verecek tek kişi benim"),
        ],
    )
    await session_repo.save(session)

    high_champ = CHAMPExtractionResult(
        challenges_score=22, authority_score=23, money_score=24, prioritization_score=20,
        challenges_confidence=0.9, authority_confidence=0.9,
        money_confidence=0.9, prioritization_confidence=0.8,
        confidence="high",
    )

    handoff_called = False

    async def fake_handoff(session_id):
        nonlocal handoff_called
        handoff_called = True
        return {"status": "handoff_complete"}

    with (
        patch("app.application.scoring.champ_extractor.local_llm_client.extract_champ",
              new_callable=AsyncMock, return_value=high_champ),
        patch("app.infrastructure.redis.client.get_redis", return_value=fake_redis),
        patch(
            "app.application.qualification.handler.HandoffHandler.handle",
            side_effect=fake_handoff,
        ),
    ):
        await extract_champ_task("sess-handoff", session_repo, score_repo)

    assert handoff_called


@pytest.mark.asyncio
async def test_low_champ_score_does_not_trigger_handoff(session_repo, score_repo, fake_redis):
    """Low CHAMP score should update score but NOT trigger handoff."""
    session = ConversationSession(
        session_id="sess-low",
        lead_json={"contact": {"name": "Test"}, "project_type": "other"},
        score=30,
        stage=SessionStage.CHAT,
        messages=[ChatMessage(role="user", content="Belki ileride düşünürüz")],
    )
    await session_repo.save(session)

    low_champ = CHAMPExtractionResult(
        challenges_score=5, authority_score=5, money_score=5, prioritization_score=5,
        challenges_confidence=0.2, authority_confidence=0.2,
        money_confidence=0.2, prioritization_confidence=0.2,
        confidence="low",
    )

    handoff_called = False

    async def fake_handoff(session_id):
        nonlocal handoff_called
        handoff_called = True

    with (
        patch("app.application.scoring.champ_extractor.local_llm_client.extract_champ",
              new_callable=AsyncMock, return_value=low_champ),
        patch("app.infrastructure.redis.client.get_redis", return_value=fake_redis),
        patch("app.application.qualification.handler.HandoffHandler.handle",
              side_effect=fake_handoff),
    ):
        await extract_champ_task("sess-low", session_repo, score_repo)

    assert not handoff_called
    updated = await session_repo.get("sess-low")
    # Score should be at least the old score (monotonic)
    assert updated.score >= 30
