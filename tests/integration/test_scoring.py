"""Integration tests for scoring pipeline and BANT dedup."""
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, patch

from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.domain.conversation.session import ConversationSession, SessionStage, ChatMessage
from app.application.scoring.bant_extractor import extract_bant_task
from app.infrastructure.llm.schemas import BANTExtractionResult


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def session_repo(fake_redis):
    return SessionRepository(fake_redis, ttl=3600)


@pytest.fixture
def score_repo(fake_redis):
    return ScoreRepository(fake_redis)


def make_session(session_id="sess-001", score=40) -> ConversationSession:
    return ConversationSession(
        session_id=session_id,
        lead_json={"contact": {"name": "Test"}, "project_type": "residential"},
        score=score,
        stage=SessionStage.CHAT,
        messages=[
            ChatMessage(role="user", content="Bütçem 3 milyon TL"),
            ChatMessage(role="assistant", content="Harika, projeniz ne zaman başlayacak?"),
            ChatMessage(role="user", content="2 ay içinde"),
        ],
    )


@pytest.mark.asyncio
async def test_bant_extraction_updates_score(session_repo, score_repo, fake_redis, monkeypatch):
    """BANT extraction should update session score in Redis."""
    session = make_session(score=40)
    await session_repo.save(session)

    mock_bant = BANTExtractionResult(
        budget_score=20, authority_score=15, need_score=18, timeline_score=20,
        confidence="high",
    )

    with (
        patch("app.application.scoring.bant_extractor.local_llm_client.extract_bant",
              new_callable=AsyncMock, return_value=mock_bant),
        patch("app.infrastructure.redis.client.get_redis", return_value=fake_redis),
    ):
        await extract_bant_task("sess-001", session_repo, score_repo)

    updated = await session_repo.get("sess-001")
    assert updated is not None
    assert updated.score == 73  # max(40, 20+15+18+20=73)
    assert updated.bant_json is not None


@pytest.mark.asyncio
async def test_bant_dedup_lock_prevents_double_execution(session_repo, score_repo, fake_redis):
    """Two concurrent BANT tasks for same session — only one should run."""
    session = make_session()
    await session_repo.save(session)

    call_count = 0

    async def fake_extract(text):
        nonlocal call_count
        call_count += 1
        return BANTExtractionResult(
            budget_score=10, authority_score=10, need_score=10, timeline_score=10
        )

    with (
        patch("app.application.scoring.bant_extractor.local_llm_client.extract_bant",
              side_effect=fake_extract),
        patch("app.infrastructure.redis.client.get_redis", return_value=fake_redis),
    ):
        import asyncio
        # Run two tasks concurrently
        await asyncio.gather(
            extract_bant_task("sess-001", session_repo, score_repo),
            extract_bant_task("sess-001", session_repo, score_repo),
        )

    # Only one extraction should have actually run
    assert call_count <= 1


@pytest.mark.asyncio
async def test_bant_skips_handoff_session(session_repo, score_repo):
    """BANT extraction should silently skip sessions already in HANDOFF stage."""
    session = make_session()
    session.stage = SessionStage.HANDOFF
    await session_repo.save(session)

    call_count = 0

    async def fake_extract(text):
        nonlocal call_count
        call_count += 1
        return BANTExtractionResult(budget_score=25, authority_score=25, need_score=25, timeline_score=25)

    with patch("app.application.scoring.bant_extractor.local_llm_client.extract_bant",
               side_effect=fake_extract):
        await extract_bant_task("sess-001", session_repo, score_repo)

    assert call_count == 0
