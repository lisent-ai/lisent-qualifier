"""Phase 2.F — Multi-framework + config endpoint tests."""

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
    pytest.mark.skipif(not _HAS_DEPS, reason="asyncpg or httpx missing"),
]


APP_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://app:qualifierpass@host.docker.internal:5433/lead_qualifier",
)


@pytest.fixture
async def tenant_slug():
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        await conn.execute("DELETE FROM tenants WHERE slug='config-test'")
        await conn.execute(
            """
            INSERT INTO tenants (slug, name, source_type, plan, status, config)
            VALUES ('config-test', 'Config Test', 'standalone', 'pro', 'active',
                    '{"qualification_threshold": 70}'::jsonb)
            """
        )
        yield "config-test"
        await conn.execute("DELETE FROM tenants WHERE slug='config-test'")
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


class TestGetConfig:
    async def test_returns_framework_and_config(self, api_client, tenant_slug):
        resp = await api_client.get("/v1/config", headers=_auth(tenant_slug))
        assert resp.status_code == 200
        body = resp.json()
        assert body["qualification_framework"] == "champ"
        assert body["config"]["qualification_threshold"] == 70
        assert set(body["supported_frameworks"]) == {"champ", "bant", "meddic"}


class TestPatchConfig:
    async def test_change_framework_to_bant(self, api_client, tenant_slug):
        resp = await api_client.patch(
            "/v1/config",
            headers=_auth(tenant_slug),
            json={"qualification_framework": "bant"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["qualification_framework"] == "bant"

        # Persisted: GET returns bant
        get_resp = await api_client.get("/v1/config", headers=_auth(tenant_slug))
        assert get_resp.json()["qualification_framework"] == "bant"

    async def test_update_threshold_merges_config(self, api_client, tenant_slug):
        resp = await api_client.patch(
            "/v1/config",
            headers=_auth(tenant_slug),
            json={"qualification_threshold": 85, "handoff_aggressiveness": "aggressive"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["qualification_threshold"] == 85
        assert body["config"]["handoff_aggressiveness"] == "aggressive"

    async def test_invalid_framework_rejected(self, api_client, tenant_slug):
        resp = await api_client.patch(
            "/v1/config",
            headers=_auth(tenant_slug),
            json={"qualification_framework": "spin"},
        )
        assert resp.status_code == 422  # Pydantic enum validation

    async def test_invalid_threshold_rejected(self, api_client, tenant_slug):
        resp = await api_client.patch(
            "/v1/config",
            headers=_auth(tenant_slug),
            json={"qualification_threshold": 150},
        )
        assert resp.status_code == 422

    async def test_empty_patch_is_noop(self, api_client, tenant_slug):
        resp = await api_client.patch("/v1/config", headers=_auth(tenant_slug), json={})
        assert resp.status_code == 200
