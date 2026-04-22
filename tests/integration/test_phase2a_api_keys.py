"""
Phase 2.A — API key system integration tests.

End-to-end:
    1. Test tenant yarat
    2. POST /v1/api-keys (slug auth ile) → raw key + public record
    3. Raw API key ile GET /v1/tenant/me → 200
    4. GET /v1/api-keys → public list (hash yok)
    5. DELETE /v1/api-keys/{id} → 204
    6. Revoked key ile GET /v1/tenant/me → 401
"""

from __future__ import annotations

import os

import pytest

try:
    import asyncpg
    from httpx import ASGITransport, AsyncClient

    _HAS_DEPS = True
except ImportError:  # pragma: no cover
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
async def admin_conn():
    try:
        conn = await asyncpg.connect(APP_DB_URL)
    except Exception as exc:
        pytest.skip(f"DB unreachable: {exc}")
    try:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='tenant_api_keys')"
        )
        if not exists:
            pytest.skip("migration 003 not applied")
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def test_tenant(admin_conn):
    await admin_conn.execute("DELETE FROM tenants WHERE slug='apikey-test'")
    await admin_conn.execute(
        """
        INSERT INTO tenants (slug, name, source_type, plan, status)
        VALUES ('apikey-test', 'API Key Test', 'standalone', 'pro', 'active')
        """
    )
    tenant_id = await admin_conn.fetchval("SELECT id FROM tenants WHERE slug='apikey-test'")
    yield {"id": tenant_id, "slug": "apikey-test"}
    await admin_conn.execute("DELETE FROM tenants WHERE slug='apikey-test'")


@pytest.fixture
async def api_client():
    from app.main import app as _app

    async with _app.router.lifespan_context(_app):
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _slug_auth_headers(slug: str) -> dict[str, str]:
    """Dev convenience — slug-based auth (Phase 1.E dev mode)."""
    return {"Authorization": f"Bearer {slug}"}


class TestCreateAPIKey:
    async def test_create_returns_raw_key_once(self, api_client, test_tenant):
        resp = await api_client.post(
            "/v1/api-keys",
            headers=await _slug_auth_headers(test_tenant["slug"]),
            json={"name": "Test Integration", "scopes": ["lead:read", "lead:write"]},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["raw_key"].startswith("sk_live_")
        assert len(body["raw_key"]) >= 40
        assert body["last_4"] == body["raw_key"][-4:]
        assert body["name"] == "Test Integration"
        assert body["scopes"] == ["lead:read", "lead:write"]
        assert body["prefix"] == "sk_live_"
        assert body["revoked_at"] is None

    async def test_create_test_env_prefix(self, api_client, test_tenant):
        resp = await api_client.post(
            "/v1/api-keys",
            headers=await _slug_auth_headers(test_tenant["slug"]),
            json={"name": "Test", "environment": "test"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["raw_key"].startswith("sk_test_")


class TestAuthWithAPIKey:
    async def test_valid_api_key_returns_tenant(self, api_client, test_tenant):
        # 1. Create key with slug auth
        create_resp = await api_client.post(
            "/v1/api-keys",
            headers=await _slug_auth_headers(test_tenant["slug"]),
            json={"name": "Auth Test"},
        )
        raw_key = create_resp.json()["raw_key"]

        # 2. Use raw key to auth
        me_resp = await api_client.get(
            "/v1/tenant/me", headers={"Authorization": f"Bearer {raw_key}"}
        )
        assert me_resp.status_code == 200, me_resp.text
        assert me_resp.json()["slug"] == "apikey-test"

    async def test_invalid_api_key_returns_401(self, api_client):
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={"Authorization": "Bearer sk_live_doesnotexist1234567890abcdef123"},
        )
        assert resp.status_code == 401


class TestListAPIKeys:
    async def test_list_excludes_hash(self, api_client, test_tenant):
        # Create 2 keys
        for i in range(2):
            await api_client.post(
                "/v1/api-keys",
                headers=await _slug_auth_headers(test_tenant["slug"]),
                json={"name": f"Key {i}"},
            )

        resp = await api_client.get(
            "/v1/api-keys", headers=await _slug_auth_headers(test_tenant["slug"])
        )
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) == 2
        for k in keys:
            assert "hash" not in k
            assert "raw_key" not in k
            assert k["name"] in {"Key 0", "Key 1"}
            assert k["last_4"]
            assert k["prefix"] == "sk_live_"


class TestRevokeAPIKey:
    async def test_revoke_blocks_subsequent_auth(self, api_client, test_tenant):
        # 1. Create key
        create_resp = await api_client.post(
            "/v1/api-keys",
            headers=await _slug_auth_headers(test_tenant["slug"]),
            json={"name": "To Revoke"},
        )
        raw_key = create_resp.json()["raw_key"]
        key_id = create_resp.json()["id"]

        # 2. Sanity check: key works
        ok = await api_client.get("/v1/tenant/me", headers={"Authorization": f"Bearer {raw_key}"})
        assert ok.status_code == 200

        # 3. Revoke
        del_resp = await api_client.delete(
            f"/v1/api-keys/{key_id}",
            headers=await _slug_auth_headers(test_tenant["slug"]),
        )
        assert del_resp.status_code == 204

        # 4. Old key no longer works
        bad = await api_client.get(
            "/v1/tenant/me", headers={"Authorization": f"Bearer {raw_key}"}
        )
        assert bad.status_code == 401

    async def test_revoke_unknown_id_returns_404(self, api_client, test_tenant):
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = await api_client.delete(
            f"/v1/api-keys/{fake_id}",
            headers=await _slug_auth_headers(test_tenant["slug"]),
        )
        assert resp.status_code == 404

    async def test_revoked_keys_hidden_by_default(self, api_client, test_tenant):
        create_resp = await api_client.post(
            "/v1/api-keys",
            headers=await _slug_auth_headers(test_tenant["slug"]),
            json={"name": "Hidden"},
        )
        key_id = create_resp.json()["id"]
        await api_client.delete(
            f"/v1/api-keys/{key_id}",
            headers=await _slug_auth_headers(test_tenant["slug"]),
        )

        # Default: revoked gizli
        resp = await api_client.get(
            "/v1/api-keys", headers=await _slug_auth_headers(test_tenant["slug"])
        )
        assert all(k["id"] != key_id for k in resp.json())

        # include_revoked=true ile görünür
        resp2 = await api_client.get(
            "/v1/api-keys?include_revoked=true",
            headers=await _slug_auth_headers(test_tenant["slug"]),
        )
        assert any(k["id"] == key_id for k in resp2.json())
