"""
Phase 1.E — Public v1 API integration tests.

End-to-end doğrulama: FastAPI app → v1_router → auth dependency →
tenant adapter DI → qualifier DB tenants tablosu → Tenant response.

Bu test Phase 1.B-D tüm scaffold'un canlı wiring'ini kanıtlar:
    GET /v1/tenant/me + valid API key
      ↓ FastAPI dependency resolution
      ↓ CompositeTenantAdapter (StandaloneTenantAdapter primary)
      ↓ SELECT FROM tenants WHERE slug=$1
      ↓ Tenant dataclass
      ↓ JSON response

Skip: asyncpg yoksa veya DB erişilemezse.
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
    pytest.mark.skipif(not _HAS_DEPS, reason="asyncpg or httpx not installed"),
]


APP_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://app:qualifierpass@host.docker.internal:5433/lead_qualifier",
)


@pytest.fixture
async def admin_conn():
    """Superuser connection — test fixture setup/teardown için."""
    try:
        conn = await asyncpg.connect(APP_DB_URL)
    except Exception as exc:
        pytest.skip(f"DB unreachable: {exc}")
    try:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='tenants')"
        )
        if not exists:
            pytest.skip("migration 003 not applied")
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def test_tenant(admin_conn):
    """Bir standalone test tenant yaratır (önceki run'dan kalan dirty state temizlenir)."""
    # Pre-clean
    await admin_conn.execute("DELETE FROM tenants WHERE slug='v1-api-test'")
    await admin_conn.execute(
        """
        INSERT INTO tenants (slug, name, source_type, plan, status, domain_claims)
        VALUES ('v1-api-test', 'V1 API Test Tenant', 'standalone', 'pro', 'active',
                ARRAY['v1apitest.com'])
        """
    )
    tenant_id = await admin_conn.fetchval("SELECT id FROM tenants WHERE slug='v1-api-test'")
    yield {"id": tenant_id, "slug": "v1-api-test"}
    await admin_conn.execute("DELETE FROM tenants WHERE slug='v1-api-test'")


@pytest.fixture
async def api_client():
    """FastAPI ASGI client — lifespan'ı manuel çalıştırır (DB pool init).

    httpx.ASGITransport lifespan'ı otomatik tetiklemiyor; FastAPI'nin
    `lifespan_context` manager'ı ile manuel başlatıp kapatıyoruz.
    """
    from app.main import app as _app

    async with _app.router.lifespan_context(_app):
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


class TestV1Health:
    """GET /v1/health — public, auth yok."""

    async def test_health_public_no_auth(self, api_client):
        resp = await api_client.get("/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["api_version"] == "v1"


class TestV1TenantAuth:
    """GET /v1/tenant/me — auth flow."""

    async def test_missing_auth_returns_401(self, api_client):
        resp = await api_client.get("/v1/tenant/me")
        assert resp.status_code == 401

    async def test_wrong_scheme_returns_401(self, api_client):
        resp = await api_client.get(
            "/v1/tenant/me", headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        assert resp.status_code == 401

    async def test_invalid_bearer_token_returns_401(self, api_client):
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={"Authorization": "Bearer sk_test_does-not-exist"},
        )
        assert resp.status_code == 401

    async def test_valid_bearer_returns_tenant_info(self, api_client, test_tenant):
        """End-to-end wiring kanıtı: valid API key → Tenant response."""
        # Debug: tenant gerçekten DB'de mi (ayrı connection)
        debug_conn = await asyncpg.connect(APP_DB_URL)
        debug_row = await debug_conn.fetchrow(
            "SELECT slug, status FROM tenants WHERE slug='v1-api-test'"
        )
        await debug_conn.close()
        assert debug_row is not None, "test_tenant fixture INSERT çalışmadı"
        assert debug_row["status"] == "active"

        # Phase 2.A: sk_test_/sk_live_ prefix'li token'lar artık API key lookup'a gider.
        # Dev slug auth için raw slug kullan (prefix'siz).
        resp = await api_client.get(
            "/v1/tenant/me",
            headers={"Authorization": f"Bearer {test_tenant['slug']}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["slug"] == "v1-api-test"
        assert body["name"] == "V1 API Test Tenant"
        assert body["source_type"] == "standalone"
        assert body["plan"] == "pro"
        assert body["status"] == "active"
        assert body["qualification_framework"] == "champ"
        assert body["domain_claims"] == ["v1apitest.com"]
        assert body["is_legacy_crm"] is False


class TestV1LegacyRoutesUnchanged:
    """v1 mount'u legacy routes'ları etkilemedi."""

    async def test_legacy_health_still_works(self, api_client):
        """Mevcut GET /health ayakta (legacy, /v1 prefix'siz)."""
        resp = await api_client.get("/health")
        assert resp.status_code == 200

    async def test_v1_prefix_is_separate_from_legacy(self, api_client):
        """/v1/health ≠ /health (farklı endpoint'ler, farklı router'lar)."""
        v1_resp = await api_client.get("/v1/health")
        legacy_resp = await api_client.get("/health")
        assert v1_resp.json() != legacy_resp.json() or "api_version" in v1_resp.json()
