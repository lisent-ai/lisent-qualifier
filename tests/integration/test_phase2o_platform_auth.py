"""Phase 2.O.1 — Platform admin token auth (BFF proxy path)."""

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

# Test senaryolarında env PLATFORM_ADMIN_TOKEN='test-bff-token' set edilmeli
PLATFORM_TOKEN = os.getenv("PLATFORM_ADMIN_TOKEN", "test-bff-token")


@pytest.fixture
async def tenant_row():
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        await conn.execute(
            """
            DELETE FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug='platform-test'
            );
            DELETE FROM tenants WHERE slug IN ('platform-test','platform-test-suspended');
            INSERT INTO tenants (slug, name, source_type, plan, status)
            VALUES ('platform-test', 'Platform Test', 'standalone', 'pro', 'active'),
                   ('platform-test-suspended', 'Suspended', 'standalone', 'pro', 'suspended')
            """
        )
        active = await conn.fetchrow(
            "SELECT id, slug FROM tenants WHERE slug='platform-test'"
        )
        suspended = await conn.fetchrow(
            "SELECT id, slug FROM tenants WHERE slug='platform-test-suspended'"
        )
        yield {"active": dict(active), "suspended": dict(suspended)}
        await conn.execute(
            """
            DELETE FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug IN ('platform-test','platform-test-suspended')
            );
            DELETE FROM tenants WHERE slug IN ('platform-test','platform-test-suspended')
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


class TestPlatformAdminAuth:
    async def test_platform_token_with_tenant_header_resolves_tenant(
        self, api_client, tenant_row
    ):
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={
                "Authorization": f"Bearer {PLATFORM_TOKEN}",
                "X-Lisent-Tenant-Id": str(tenant_row["active"]["id"]),
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["slug"] == "platform-test"

    async def test_platform_token_missing_header_returns_400(
        self, api_client, tenant_row
    ):
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={"Authorization": f"Bearer {PLATFORM_TOKEN}"},
        )
        assert resp.status_code == 400
        assert "X-Lisent-Tenant-Id" in resp.json()["detail"]

    async def test_platform_token_invalid_uuid_returns_400(
        self, api_client, tenant_row
    ):
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={
                "Authorization": f"Bearer {PLATFORM_TOKEN}",
                "X-Lisent-Tenant-Id": "not-a-uuid",
            },
        )
        assert resp.status_code == 400

    async def test_platform_token_unknown_tenant_returns_404(
        self, api_client, tenant_row
    ):
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={
                "Authorization": f"Bearer {PLATFORM_TOKEN}",
                "X-Lisent-Tenant-Id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert resp.status_code == 404

    async def test_platform_token_suspended_tenant_returns_403(
        self, api_client, tenant_row
    ):
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={
                "Authorization": f"Bearer {PLATFORM_TOKEN}",
                "X-Lisent-Tenant-Id": str(tenant_row["suspended"]["id"]),
            },
        )
        assert resp.status_code == 403

    async def test_wrong_platform_token_falls_through_to_slug_auth(
        self, api_client, tenant_row
    ):
        """Yanlış platform token + valid slug → slug auth'a düşer."""
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={"Authorization": "Bearer totally-wrong-token-xxxx"},
        )
        # Slug lookup 'totally-wrong-token-xxxx' bulamaz → 401
        assert resp.status_code == 401


class TestPlatformTokenViaSourceRef:
    """BFF'nin CRM company_id'sini doğrudan göndermesi — legacy tenant lookup."""

    async def test_source_ref_resolves_crm_tenant(self, api_client, tenant_row):
        """Önce legacy tenant yarat (migration'ın yaptığı), sonra source_ref ile resolve."""
        import asyncpg

        conn = await asyncpg.connect(APP_DB_URL)
        try:
            await conn.execute(
                "DELETE FROM tenants WHERE slug='legacy-for-sourceref-test'"
            )
            fake_company_id = "aa111111-2222-3333-4444-555555555555"
            await conn.execute(
                """
                INSERT INTO tenants (slug, name, source_type, source_ref, plan, status)
                VALUES ('legacy-for-sourceref-test', 'Legacy', 'lisent_crm', $1, 'legacy', 'active')
                """,
                fake_company_id,
            )

            resp = await api_client.get(
                "/v1/tenant/me",
                headers={
                    "Authorization": f"Bearer {PLATFORM_TOKEN}",
                    "X-Lisent-Source-Ref": fake_company_id,
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["source_type"] == "lisent_crm"
            assert body["is_legacy_crm"] is True
        finally:
            await conn.execute(
                "DELETE FROM tenants WHERE slug='legacy-for-sourceref-test'"
            )
            await conn.close()

    async def test_unknown_source_ref_returns_404(self, api_client):
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={
                "Authorization": f"Bearer {PLATFORM_TOKEN}",
                "X-Lisent-Source-Ref": "does-not-exist-uuid-xxx",
            },
        )
        assert resp.status_code == 404

    async def test_invalid_source_type_returns_400(self, api_client):
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={
                "Authorization": f"Bearer {PLATFORM_TOKEN}",
                "X-Lisent-Source-Ref": "x",
                "X-Lisent-Source-Type": "invalid_type",
            },
        )
        assert resp.status_code == 400


class TestPlatformTokenAllowsAllV1Operations:
    async def test_platform_can_create_api_key_for_tenant(
        self, api_client, tenant_row
    ):
        resp = await api_client.post(
            "/v1/api-keys",
            headers={
                "Authorization": f"Bearer {PLATFORM_TOKEN}",
                "X-Lisent-Tenant-Id": str(tenant_row["active"]["id"]),
            },
            json={"name": "Platform-managed key"},
        )
        assert resp.status_code == 201
        assert resp.json()["tenant_id"] == str(tenant_row["active"]["id"])

    async def test_platform_can_read_config(self, api_client, tenant_row):
        resp = await api_client.get(
            "/v1/config",
            headers={
                "Authorization": f"Bearer {PLATFORM_TOKEN}",
                "X-Lisent-Tenant-Id": str(tenant_row["active"]["id"]),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["qualification_framework"] == "champ"
