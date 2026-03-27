"""
Integration tests: POST /webhook/lead
Uses fakeredis via FastAPI dependency overrides.
"""
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api import deps
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def override_redis_deps(fake_redis):
    """Override FastAPI deps to use fakeredis for all webhook tests."""
    def _fake_session_repo():
        return SessionRepository(fake_redis, ttl=3600)

    def _fake_score_repo():
        return ScoreRepository(fake_redis)

    app.dependency_overrides[deps._get_session_repo] = _fake_session_repo
    app.dependency_overrides[deps._get_score_repo] = _fake_score_repo
    yield
    app.dependency_overrides.clear()


HIGH_SCORE_PAYLOAD = {
    "name": "Ali Veli",
    "phone": "05001234567",
    "email": "ali@example.com",
    "city": "Istanbul",
    "source": "instagram",
    "project_type": "commercial",
    "budget_range": "over_10m",
    "budget_amount": 15000000,
    "decision_authority": "sole",
    "timeline_urgency": "immediate",
    "notes": "Büyük ticari proje, acil başlamak istiyoruz.",
}

LOW_SCORE_PAYLOAD = {
    "name": "Veli Ali",
    "phone": "05009876543",
    "source": "facebook",
    "project_type": "other",
    "budget_range": "unknown",
    "decision_authority": "unknown",
    "timeline_urgency": "unknown",
}


@pytest.mark.asyncio
async def test_high_score_fast_path():
    """Score >= 80 lead should be fast-pathed."""

    class FakeReport:
        def model_dump(self):
            return {
                "summary": "test",
                "score_explanation": "high",
                "key_signals": [],
                "recommended_approach": "call",
                "potential_objections": [],
                "priority": "high",
            }

    with (
        patch(
            "app.application.lead_intake.handler.local_llm_client.generate_reasoning_report",
            new_callable=AsyncMock,
            return_value=FakeReport(),
        ),
        patch(
            "app.application.lead_intake.handler.send_to_crm",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/webhook/lead", json=HIGH_SCORE_PAYLOAD)

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "fast_path"
    assert data["score"] >= 80
    assert data["crm_sent"] is True


@pytest.mark.asyncio
async def test_low_score_chat_path_creates_session(fake_redis):
    """Score < 80 lead should create a Redis session and return session_id."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/webhook/lead", json=LOW_SCORE_PAYLOAD)

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "chat_path"
    assert "session_id" in data
    assert data["score"] < 80

    # Session should exist in Redis
    exists = await fake_redis.exists(f"session:{data['session_id']}")
    assert exists == 1


@pytest.mark.asyncio
async def test_duplicate_lead_ignored():
    """Same lead_id submitted twice should return 'duplicate' on second attempt."""
    payload = {**LOW_SCORE_PAYLOAD, "lead_id": "dedup-test-001"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp1 = await ac.post("/webhook/lead", json=payload)
        resp2 = await ac.post("/webhook/lead", json=payload)

    assert resp1.status_code == 202
    assert resp2.status_code == 202
    assert resp2.json()["status"] == "duplicate"


@pytest.mark.asyncio
async def test_invalid_payload_rejected():
    """Missing required fields should return 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/webhook/lead", json={"phone": "05001"})
    assert resp.status_code == 422
