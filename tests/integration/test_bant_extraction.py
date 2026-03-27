"""
Integration test: BANT score triggers handoff when threshold crossed.
"""
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, patch

from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.domain.conversation.session import ConversationSession, SessionStage, ChatMessage
from app.application.scoring.bant_extractor import extract_bant_task
from app.infrastructure.llm.schemas import BANTExtractionResult
from app.domain.scoring.thresholds import HIGH_THRESHOLD


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
async def test_high_bant_score_triggers_handoff(session_repo, score_repo, fake_redis):
    """When BANT extraction yields score >= HIGH_THRESHOLD, HandoffHandler should be called."""
    session = ConversationSession(
        session_id="sess-handoff",
        lead_json={"contact": {"name": "Test"}, "project_type": "commercial"},
        score=50,
        stage=SessionStage.CHAT,
        messages=[
            ChatMessage(role="user", content="10 milyon bütçem var ve hemen başlamak istiyorum"),
            ChatMessage(role="assistant", content="Harika, daha fazla bilgi verebilir misiniz?"),
            ChatMessage(role="user", content="Evet, karar verecek tek kişi benim"),
        ],
    )
    await session_repo.save(session)

    # BANT result that pushes score over threshold
    high_bant = BANTExtractionResult(
        budget_score=25, authority_score=25, need_score=20, timeline_score=15,
        confidence="high",
    )
    assert high_bant.total >= HIGH_THRESHOLD

    handoff_called = False

    async def fake_handoff(session_id):
        nonlocal handoff_called
        handoff_called = True
        return {"status": "handoff_complete"}

    with (
        patch("app.application.scoring.bant_extractor.local_llm_client.extract_bant",
              new_callable=AsyncMock, return_value=high_bant),
        patch("app.infrastructure.redis.client.get_redis", return_value=fake_redis),
        patch(
            "app.application.qualification.handler.HandoffHandler.handle",
            side_effect=fake_handoff,
        ),
    ):
        await extract_bant_task("sess-handoff", session_repo, score_repo)

    assert handoff_called


@pytest.mark.asyncio
async def test_low_bant_score_does_not_trigger_handoff(session_repo, score_repo, fake_redis):
    """Low BANT score should update score but NOT trigger handoff."""
    session = ConversationSession(
        session_id="sess-low",
        lead_json={"contact": {"name": "Test"}},
        score=30,
        stage=SessionStage.CHAT,
        messages=[ChatMessage(role="user", content="Belki ileride düşünürüz")],
    )
    await session_repo.save(session)

    low_bant = BANTExtractionResult(
        budget_score=5, authority_score=5, need_score=5, timeline_score=5,
        confidence="low",
    )

    handoff_called = False

    async def fake_handoff(session_id):
        nonlocal handoff_called
        handoff_called = True

    with (
        patch("app.application.scoring.bant_extractor.local_llm_client.extract_bant",
              new_callable=AsyncMock, return_value=low_bant),
        patch("app.infrastructure.redis.client.get_redis", return_value=fake_redis),
        patch("app.application.qualification.handler.HandoffHandler.handle",
              side_effect=fake_handoff),
    ):
        await extract_bant_task("sess-low", session_repo, score_repo)

    assert not handoff_called
    updated = await session_repo.get("sess-low")
    assert updated.score == 30  # max(30, 5+5+5+5=20) → stays 30
