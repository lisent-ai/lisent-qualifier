"""Phase 2.G — Usage endpoint integration tests (end-to-end counter)."""

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
async def tenant_slug():
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        await conn.execute(
            """
            DELETE FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug='usage-test'
            );
            DELETE FROM tenants WHERE slug='usage-test';
            INSERT INTO tenants (slug, name, source_type, plan, status)
            VALUES ('usage-test', 'Usage Test', 'standalone', 'free', 'active')
            """
        )
        yield "usage-test"
        await conn.execute(
            """
            DELETE FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug='usage-test'
            );
            DELETE FROM tenants WHERE slug='usage-test'
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


class TestUsageEndpoint:
    async def test_usage_reflects_leads_ingested(self, api_client, tenant_slug):
        # Create 3 leads
        for i in range(3):
            await api_client.post(
                "/v1/leads", headers=_auth(tenant_slug), json={"external_ref": f"u-{i}"}
            )

        resp = await api_client.get("/v1/usage", headers=_auth(tenant_slug))
        assert resp.status_code == 200
        body = resp.json()
        assert body["plan"] == "free"
        assert body["today"]["leads_ingested"] >= 3
        assert body["rate_limit_per_min"] == 60

    async def test_usage_zero_when_no_activity(self, api_client, tenant_slug):
        # Redis counter might have leftover from other tests — clear
        import fakeredis.aioredis  # not used, just ensure import
        from app.infrastructure.redis.client import get_redis
        from app.infrastructure.usage.counter import RedisUsageCounter

        redis = get_redis()
        # Clear all usage keys for this tenant
        async for key in redis.scan_iter(match="usage:*"):
            await redis.delete(key)

        resp = await api_client.get("/v1/usage", headers=_auth(tenant_slug))
        assert resp.status_code == 200
        body = resp.json()
        for metric in body["today"]:
            assert body["today"][metric] == 0
