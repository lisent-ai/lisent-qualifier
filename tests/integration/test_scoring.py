"""Integration tests for scoring pipeline and CHAMP dedup."""
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
async def test_champ_extraction_updates_score(session_repo, score_repo, fake_redis, monkeypatch):
    """CHAMP extraction should update session score in Redis."""
    session = make_session(score=40)
    await session_repo.save(session)

    mock_champ = CHAMPExtractionResult(
        challenges_score=20, authority_score=15, money_score=18, prioritization_score=20,
        confidence="high",
    )

    with (
        patch("app.application.scoring.champ_extractor.local_llm_client.extract_champ",
              new_callable=AsyncMock, return_value=mock_champ),
        patch("app.infrastructure.redis.client.get_redis", return_value=fake_redis),
    ):
        await extract_champ_task("sess-001", session_repo, score_repo)

    updated = await session_repo.get("sess-001")
    assert updated is not None
    assert updated.score >= 40  # composite score should be at least old score (monotonic)
    assert updated.champ_json is not None


@pytest.mark.asyncio
async def test_champ_dedup_lock_prevents_double_execution(session_repo, score_repo, fake_redis):
    """Two concurrent CHAMP tasks for same session — only one should run."""
    session = make_session()
    await session_repo.save(session)

    call_count = 0

    async def fake_extract(text, prompt=None):
        nonlocal call_count
        call_count += 1
        return CHAMPExtractionResult(
            challenges_score=10, authority_score=10, money_score=10, prioritization_score=10
        )

    with (
        patch("app.application.scoring.champ_extractor.local_llm_client.extract_champ",
              side_effect=fake_extract),
        patch("app.infrastructure.redis.client.get_redis", return_value=fake_redis),
    ):
        import asyncio
        await asyncio.gather(
            extract_champ_task("sess-001", session_repo, score_repo),
            extract_champ_task("sess-001", session_repo, score_repo),
        )

    assert call_count <= 1


@pytest.mark.asyncio
async def test_champ_skips_handoff_session(session_repo, score_repo):
    """CHAMP extraction should silently skip sessions already in HANDOFF stage."""
    session = make_session()
    session.stage = SessionStage.HANDOFF
    await session_repo.save(session)

    call_count = 0

    async def fake_extract(text, prompt=None):
        nonlocal call_count
        call_count += 1
        return CHAMPExtractionResult(
            challenges_score=25, authority_score=25, money_score=25, prioritization_score=25,
        )

    with patch("app.application.scoring.champ_extractor.local_llm_client.extract_champ",
               side_effect=fake_extract):
        await extract_champ_task("sess-001", session_repo, score_repo)

    assert call_count == 0
