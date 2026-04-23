"""
Phase 2.D — Live score stream (SSE) integration tests.

Covered:
    - Auth: missing bearer → 401
    - Tenant scoping: lead without session → 404
    - Cross-tenant: tenant A API key + tenant B lead → 404
    - Happy path: EventPort.publish → client receives a `score.updated` frame
    - Replay: publish two events, reconnect with Last-Event-ID of the first,
      only the second event is re-emitted

Requires the dev stack (Postgres 5433 + Redis 6380).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime

import pytest

try:
    import asyncpg
    import redis.asyncio as redis_async
    from httpx import ASGITransport, AsyncClient

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _HAS_DEPS, reason="asyncpg, redis, or httpx missing"),
]


APP_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://app:qualifierpass@host.docker.internal:5433/lead_qualifier",
)
REDIS_DSN = os.getenv("REDIS_DSN", "redis://host.docker.internal:6380/0")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def admin_conn():
    try:
        conn = await asyncpg.connect(APP_DB_URL)
    except Exception as exc:
        pytest.skip(f"DB unreachable: {exc}")
    try:
        yield conn
    finally:
        await conn.close()


async def _reset_tenant(admin_conn, slug: str) -> None:
    # asyncpg prepare() forbids multi-statement SQL when $N params are used —
    # split into three single-statement executes.
    await admin_conn.execute(
        """
        DELETE FROM qualifier_sessions WHERE lead_id IN (
            SELECT id FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug=$1
            )
        )
        """,
        slug,
    )
    await admin_conn.execute(
        "DELETE FROM qualifier_leads WHERE tenant_id IN (SELECT id FROM tenants WHERE slug=$1)",
        slug,
    )
    await admin_conn.execute("DELETE FROM tenants WHERE slug=$1", slug)


@pytest.fixture
async def tenant_a(admin_conn):
    await _reset_tenant(admin_conn, "sse-test-a")
    await admin_conn.execute(
        """
        INSERT INTO tenants (slug, name, source_type, plan, status, config)
        VALUES ('sse-test-a', 'SSE Test A', 'standalone', 'pro', 'active', '{}'::jsonb)
        """
    )
    row = await admin_conn.fetchrow("SELECT id, slug FROM tenants WHERE slug='sse-test-a'")
    yield dict(row)
    await _reset_tenant(admin_conn, "sse-test-a")


@pytest.fixture
async def tenant_b(admin_conn):
    await _reset_tenant(admin_conn, "sse-test-b")
    await admin_conn.execute(
        """
        INSERT INTO tenants (slug, name, source_type, plan, status, config)
        VALUES ('sse-test-b', 'SSE Test B', 'standalone', 'pro', 'active', '{}'::jsonb)
        """
    )
    row = await admin_conn.fetchrow("SELECT id, slug FROM tenants WHERE slug='sse-test-b'")
    yield dict(row)
    await _reset_tenant(admin_conn, "sse-test-b")


@pytest.fixture
async def api_client():
    from app.main import app as _app

    async with _app.router.lifespan_context(_app):
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://test", timeout=30.0) as client:
            yield client


@pytest.fixture
async def redis_client():
    try:
        r = redis_async.from_url(REDIS_DSN, decode_responses=False)
        await r.ping()
    except Exception as exc:
        pytest.skip(f"Redis unreachable: {exc}")
    try:
        yield r
    finally:
        await r.aclose()


def _auth(slug: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {slug}"}


async def _make_lead_with_session(admin_conn, api_client, tenant):
    create = await api_client.post(
        "/v1/leads", headers=_auth(tenant["slug"]), json={"name": "SSE Lead"}
    )
    assert create.status_code == 201, create.text
    lead_id = uuid.UUID(create.json()["id"])

    session_id = uuid.uuid4()
    await admin_conn.execute(
        """
        INSERT INTO qualifier_sessions (id, lead_id, company_id, tenant_id, score, stage, messages)
        VALUES ($1, $2, $3, $3, 0, 'chat', '[]'::jsonb)
        """,
        session_id,
        lead_id,
        tenant["id"],
    )
    return lead_id, session_id


async def _publish_event(tenant_id, lead_id, session_id, *, score, event_id_ms=None):
    """Publish a score.updated event via the production EventPort adapter."""
    from app.adapters.event import RedisPubSubAdapter
    from app.infrastructure.redis.client import get_redis
    from app.ports.event import ScoreEvent

    ts = (
        datetime.utcfromtimestamp(event_id_ms / 1000)
        if event_id_ms is not None
        else datetime.utcnow()
    )
    adapter = RedisPubSubAdapter(get_redis())
    await adapter.publish(
        ScoreEvent(
            tenant_id=tenant_id,
            lead_id=lead_id,
            session_id=session_id,
            event_type="score.updated",
            score=score,
            threshold=75,
            path="chat",
            payload={"score": score, "champ": {}},
            timestamp=ts,
        )
    )


async def _collect_sse(response, *, max_events=1, timeout=10.0):
    """Read SSE frames from an httpx streaming response, parsing event + id + data."""
    events: list[dict] = []
    current: dict = {}

    async def _iter():
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("id:"):
                current["id"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                current["data"] = line.split(":", 1)[1].strip()
            elif line == "":
                if "event" in current and "data" in current:
                    events.append(dict(current))
                    current.clear()
                    if len(events) >= max_events:
                        return

    try:
        await asyncio.wait_for(_iter(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    return events


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAuth:
    async def test_missing_bearer_returns_401(self, api_client):
        resp = await api_client.get(f"/v1/leads/{uuid.uuid4()}/score-stream")
        assert resp.status_code == 401


class TestScoping:
    async def test_lead_without_session_returns_404(self, api_client, tenant_a):
        create = await api_client.post(
            "/v1/leads", headers=_auth(tenant_a["slug"]), json={"name": "No session"}
        )
        lead_id = create.json()["id"]

        resp = await api_client.get(
            f"/v1/leads/{lead_id}/score-stream", headers=_auth(tenant_a["slug"])
        )
        assert resp.status_code == 404

    async def test_cross_tenant_returns_404(
        self, admin_conn, api_client, tenant_a, tenant_b
    ):
        lead_id, _ = await _make_lead_with_session(admin_conn, api_client, tenant_a)

        resp = await api_client.get(
            f"/v1/leads/{lead_id}/score-stream", headers=_auth(tenant_b["slug"])
        )
        assert resp.status_code == 404


class TestLiveStream:
    async def test_receives_published_event(
        self, admin_conn, api_client, tenant_a, redis_client
    ):
        lead_id, session_id = await _make_lead_with_session(
            admin_conn, api_client, tenant_a
        )

        async with api_client.stream(
            "GET",
            f"/v1/leads/{lead_id}/score-stream",
            headers=_auth(tenant_a["slug"]),
        ) as response:
            assert response.status_code == 200

            # Publish after subscription has had a moment to attach.
            await asyncio.sleep(0.2)
            await _publish_event(tenant_a["id"], lead_id, session_id, score=42)

            events = await _collect_sse(response, max_events=1, timeout=8.0)

        updates = [e for e in events if e["event"] == "score.updated"]
        assert updates, f"no score.updated frame — got {events}"
        data = json.loads(updates[0]["data"])
        assert data["score"] == 42
        assert data["lead_id"] == str(lead_id)
        assert data["session_id"] == str(session_id)
        assert updates[0]["id"] == data["event_id"]


class TestReplay:
    async def test_last_event_id_replays_only_newer(
        self, admin_conn, api_client, tenant_a
    ):
        lead_id, session_id = await _make_lead_with_session(
            admin_conn, api_client, tenant_a
        )

        first_ms = int(datetime.utcnow().timestamp() * 1000)
        await _publish_event(
            tenant_a["id"], lead_id, session_id, score=10, event_id_ms=first_ms
        )

        second_ms = first_ms + 1_000
        await _publish_event(
            tenant_a["id"], lead_id, session_id, score=20, event_id_ms=second_ms
        )

        async with api_client.stream(
            "GET",
            f"/v1/leads/{lead_id}/score-stream",
            headers={**_auth(tenant_a["slug"]), "Last-Event-ID": str(first_ms)},
        ) as response:
            assert response.status_code == 200
            events = await _collect_sse(response, max_events=1, timeout=4.0)

        updates = [e for e in events if e["event"] == "score.updated"]
        assert len(updates) == 1
        data = json.loads(updates[0]["data"])
        assert data["score"] == 20
        assert updates[0]["id"] == str(second_ms)
