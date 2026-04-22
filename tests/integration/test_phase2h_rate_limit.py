"""Phase 2.H — Rate limit integration (429 enforcement)."""

from __future__ import annotations

import os

import pytest

try:
    import asyncpg
    from httpx import ASGITransport, AsyncClient

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _HAS_DEPS, reason="deps missing"),
]


APP_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://app:qualifierpass@host.docker.internal:5433/lead_qualifier",
)


@pytest.fixture
async def free_tenant():
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        await conn.execute(
            """
            DELETE FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug='rl-free'
            );
            DELETE FROM tenants WHERE slug='rl-free';
            INSERT INTO tenants (slug, name, source_type, plan, status)
            VALUES ('rl-free', 'RL Free', 'standalone', 'free', 'active')
            """
        )
        yield "rl-free"
        await conn.execute(
            """
            DELETE FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug='rl-free'
            );
            DELETE FROM tenants WHERE slug='rl-free'
            """
        )
    finally:
        await conn.close()


@pytest.fixture
async def api_client():
    from app.main import app as _app

    async with _app.router.lifespan_context(_app):
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _auth(slug: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {slug}"}


class TestRateLimitEnforcement:
    async def test_free_plan_429_after_limit(self, api_client, free_tenant):
        """Free = 60/min. Clear Redis, hit 61 times on POST /v1/leads."""
        from app.infrastructure.redis.client import get_redis

        redis = get_redis()
        async for key in redis.scan_iter(match="rate:*"):
            await redis.delete(key)

        # 60 success
        successes = 0
        for i in range(60):
            r = await api_client.post(
                "/v1/leads",
                headers=_auth(free_tenant),
                json={"external_ref": f"rl-{i}"},
            )
            if r.status_code == 201:
                successes += 1
        assert successes == 60

        # 61st → 429 + Retry-After header
        r = await api_client.post(
            "/v1/leads", headers=_auth(free_tenant), json={"external_ref": "rl-61"}
        )
        assert r.status_code == 429
        assert "Retry-After" in r.headers
