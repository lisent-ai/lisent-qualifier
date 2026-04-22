"""
Phase 2.B — Lead CRUD + cursor pagination integration tests.

Covered:
    - POST /v1/leads — create with + without external_ref
    - POST /v1/leads — duplicate external_ref returns 409
    - GET /v1/leads/{id} — lookup within tenant
    - GET /v1/leads/{id} — 404 when not found (or cross-tenant attempt)
    - GET /v1/leads — list with status filter
    - GET /v1/leads — cursor pagination (multi-page)
    - RLS: tenant A cannot see tenant B's leads via list or lookup
"""

from __future__ import annotations

import os
import uuid

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
async def admin_conn():
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
async def two_tenants(admin_conn):
    """İki distinct tenant — RLS isolation testi için.

    Setup + teardown hem lead'leri hem tenant'ları temizler (FK).
    """

    async def _cleanup() -> None:
        await admin_conn.execute(
            """
            DELETE FROM qualifier_leads
            WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug IN ('lead-test-a','lead-test-b')
            )
            """
        )
        await admin_conn.execute(
            "DELETE FROM tenants WHERE slug IN ('lead-test-a','lead-test-b')"
        )

    await _cleanup()
    await admin_conn.execute(
        """
        INSERT INTO tenants (slug, name, source_type, plan, status)
        VALUES ('lead-test-a', 'Lead Test A', 'standalone', 'pro', 'active'),
               ('lead-test-b', 'Lead Test B', 'standalone', 'pro', 'active')
        """
    )
    a = await admin_conn.fetchrow("SELECT id, slug FROM tenants WHERE slug='lead-test-a'")
    b = await admin_conn.fetchrow("SELECT id, slug FROM tenants WHERE slug='lead-test-b'")
    yield {"a": dict(a), "b": dict(b)}
    await _cleanup()


@pytest.fixture
async def api_client():
    from app.main import app as _app

    async with _app.router.lifespan_context(_app):
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _auth(slug: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {slug}"}


class TestCreateLead:
    async def test_create_with_auto_external_ref(self, api_client, two_tenants):
        resp = await api_client.post(
            "/v1/leads",
            headers=_auth(two_tenants["a"]["slug"]),
            json={"name": "Ali", "email": "ali@example.com", "phone": "+905551112233"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Ali"
        assert body["email"] == "ali@example.com"
        assert body["phone"] == "+905551112233"
        assert body["status"] == "new"
        assert body["score"] == 0
        assert body["external_ref"]  # auto UUID
        assert body["tenant_id"] == str(two_tenants["a"]["id"])

    async def test_create_with_explicit_external_ref(self, api_client, two_tenants):
        resp = await api_client.post(
            "/v1/leads",
            headers=_auth(two_tenants["a"]["slug"]),
            json={"external_ref": "hubspot-123", "name": "Burcu"},
        )
        assert resp.status_code == 201
        assert resp.json()["external_ref"] == "hubspot-123"

    async def test_duplicate_external_ref_returns_409(self, api_client, two_tenants):
        # İlk create
        r1 = await api_client.post(
            "/v1/leads",
            headers=_auth(two_tenants["a"]["slug"]),
            json={"external_ref": "dup-1", "name": "A"},
        )
        assert r1.status_code == 201

        # Aynı ref ile tekrar → 409
        r2 = await api_client.post(
            "/v1/leads",
            headers=_auth(two_tenants["a"]["slug"]),
            json={"external_ref": "dup-1", "name": "A (retry)"},
        )
        assert r2.status_code == 409

    async def test_notes_stored_in_extra_data(self, api_client, two_tenants):
        resp = await api_client.post(
            "/v1/leads",
            headers=_auth(two_tenants["a"]["slug"]),
            json={"name": "X", "notes": "High priority lead"},
        )
        body = resp.json()
        assert body["notes"] == "High priority lead"
        assert body["extra_data"].get("notes") == "High priority lead"


class TestGetLead:
    async def test_get_returns_lead_within_tenant(self, api_client, two_tenants):
        create = await api_client.post(
            "/v1/leads",
            headers=_auth(two_tenants["a"]["slug"]),
            json={"external_ref": "get-1", "name": "Can"},
        )
        lead_id = create.json()["id"]

        resp = await api_client.get(
            f"/v1/leads/{lead_id}", headers=_auth(two_tenants["a"]["slug"])
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Can"

    async def test_get_unknown_returns_404(self, api_client, two_tenants):
        fake = "00000000-0000-0000-0000-000000000000"
        resp = await api_client.get(f"/v1/leads/{fake}", headers=_auth(two_tenants["a"]["slug"]))
        assert resp.status_code == 404

    async def test_cross_tenant_access_returns_404(self, api_client, two_tenants):
        """Tenant A'nın lead'ini Tenant B context'inde sorgulama 404 (RLS)."""
        create = await api_client.post(
            "/v1/leads",
            headers=_auth(two_tenants["a"]["slug"]),
            json={"external_ref": "xt-1", "name": "Cross"},
        )
        lead_id = create.json()["id"]

        # B context → RLS 0 row, 404
        resp = await api_client.get(f"/v1/leads/{lead_id}", headers=_auth(two_tenants["b"]["slug"]))
        assert resp.status_code == 404


class TestListLeads:
    async def test_list_returns_tenant_leads_only(self, api_client, two_tenants):
        # A'ya 3 lead
        for i in range(3):
            await api_client.post(
                "/v1/leads",
                headers=_auth(two_tenants["a"]["slug"]),
                json={"external_ref": f"A-{i}", "name": f"Lead A{i}"},
            )
        # B'ye 2 lead
        for i in range(2):
            await api_client.post(
                "/v1/leads",
                headers=_auth(two_tenants["b"]["slug"]),
                json={"external_ref": f"B-{i}", "name": f"Lead B{i}"},
            )

        resp_a = await api_client.get("/v1/leads", headers=_auth(two_tenants["a"]["slug"]))
        assert resp_a.status_code == 200
        body_a = resp_a.json()
        assert body_a["pagination"]["count"] == 3
        assert all("A" in lead["name"] for lead in body_a["data"])

        resp_b = await api_client.get("/v1/leads", headers=_auth(two_tenants["b"]["slug"]))
        assert resp_b.json()["pagination"]["count"] == 2

    async def test_cursor_pagination(self, api_client, two_tenants):
        # 5 lead
        for i in range(5):
            await api_client.post(
                "/v1/leads",
                headers=_auth(two_tenants["a"]["slug"]),
                json={"external_ref": f"page-{i}", "name": f"P{i}"},
            )

        # Page 1: limit=2
        page1 = await api_client.get(
            "/v1/leads?limit=2", headers=_auth(two_tenants["a"]["slug"])
        )
        p1 = page1.json()
        assert len(p1["data"]) == 2
        assert p1["pagination"]["has_more"] is True
        assert p1["pagination"]["next_cursor"]

        # Page 2
        cursor = p1["pagination"]["next_cursor"]
        page2 = await api_client.get(
            f"/v1/leads?limit=2&cursor={cursor}", headers=_auth(two_tenants["a"]["slug"])
        )
        p2 = page2.json()
        assert len(p2["data"]) == 2
        assert p2["pagination"]["has_more"] is True

        # Page 3 (last)
        cursor2 = p2["pagination"]["next_cursor"]
        page3 = await api_client.get(
            f"/v1/leads?limit=2&cursor={cursor2}", headers=_auth(two_tenants["a"]["slug"])
        )
        p3 = page3.json()
        assert len(p3["data"]) == 1
        assert p3["pagination"]["has_more"] is False
        assert p3["pagination"]["next_cursor"] is None

        # Tüm lead'ler unique (pagination overlap yok)
        all_ids = {l["id"] for l in p1["data"] + p2["data"] + p3["data"]}
        assert len(all_ids) == 5

    async def test_status_filter(self, api_client, two_tenants, admin_conn):
        # 3 lead yarat (default 'new')
        for i in range(3):
            await api_client.post(
                "/v1/leads",
                headers=_auth(two_tenants["a"]["slug"]),
                json={"external_ref": f"f-{i}", "name": f"F{i}"},
            )

        # Birinin status'unu manuel değiştir
        await admin_conn.execute(
            "UPDATE qualifier_leads SET status='qualified' WHERE tenant_id=$1 AND lead_id='f-0'",
            two_tenants["a"]["id"],
        )

        # Filter 'qualified' → 1 lead
        resp = await api_client.get(
            "/v1/leads?status=qualified", headers=_auth(two_tenants["a"]["slug"])
        )
        assert len(resp.json()["data"]) == 1

        # Filter 'new' → 2 lead
        resp = await api_client.get(
            "/v1/leads?status=new", headers=_auth(two_tenants["a"]["slug"])
        )
        assert len(resp.json()["data"]) == 2

    async def test_invalid_cursor_returns_400(self, api_client, two_tenants):
        resp = await api_client.get(
            "/v1/leads?cursor=not-a-valid-cursor",
            headers=_auth(two_tenants["a"]["slug"]),
        )
        assert resp.status_code == 400
