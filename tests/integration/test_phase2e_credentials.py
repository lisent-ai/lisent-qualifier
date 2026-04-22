"""Phase 2.E — Credential issuance + verification integration tests."""

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
                SELECT id FROM tenants WHERE slug='cred-test'
            );
            DELETE FROM tenants WHERE slug='cred-test';
            INSERT INTO tenants (slug, name, source_type, plan, status, config, outbound_webhook_secret)
            VALUES ('cred-test', 'Cred Test', 'standalone', 'pro', 'active',
                    '{"qualification_threshold": 70}'::jsonb, 'tenant-secret-key')
            """
        )
        yield "cred-test"
        await conn.execute(
            """
            DELETE FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug='cred-test'
            );
            DELETE FROM tenants WHERE slug='cred-test'
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


class TestIssue:
    async def test_issue_returns_signed_jwt(self, api_client, tenant_slug):
        create = await api_client.post(
            "/v1/leads",
            headers=_auth(tenant_slug),
            json={"name": "Ali", "email": "ali@example.com"},
        )
        lead_id = create.json()["id"]

        # Set score manually (scoring Phase 2'de otomatik değil)
        import asyncpg

        conn = await asyncpg.connect(APP_DB_URL)
        await conn.execute(
            "UPDATE qualifier_leads SET score=85, status='qualified' WHERE id=$1",
            __import__("uuid").UUID(lead_id),
        )
        await conn.close()

        resp = await api_client.get(
            f"/v1/leads/{lead_id}/credential", headers=_auth(tenant_slug)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["credential"].count(".") == 2  # JWT format
        assert body["score"] == 85
        assert body["threshold"] == 70
        assert body["qualified"] is True
        assert body["framework"] == "champ"


class TestVerify:
    async def test_verify_valid_credential(self, api_client, tenant_slug):
        create = await api_client.post(
            "/v1/leads",
            headers=_auth(tenant_slug),
            json={"name": "Ali", "email": "ali@example.com"},
        )
        lead_id = create.json()["id"]

        issue = await api_client.get(
            f"/v1/leads/{lead_id}/credential", headers=_auth(tenant_slug)
        )
        cred_token = issue.json()["credential"]

        verify = await api_client.post(
            "/v1/credentials/verify",
            headers=_auth(tenant_slug),
            json={"credential": cred_token},
        )
        assert verify.status_code == 200
        body = verify.json()
        assert body["valid"] is True
        assert body["score"] >= 0
        assert body["framework"] == "champ"

    async def test_verify_malformed_returns_invalid(self, api_client, tenant_slug):
        resp = await api_client.post(
            "/v1/credentials/verify",
            headers=_auth(tenant_slug),
            json={"credential": "not-a-jwt-at-all"},
        )
        assert resp.status_code == 200
        assert resp.json()["valid"] is False
        assert "malformed" in resp.json()["reason"].lower()

    async def test_verify_tampered_returns_invalid(self, api_client, tenant_slug):
        create = await api_client.post(
            "/v1/leads",
            headers=_auth(tenant_slug),
            json={"name": "A", "email": "a@x.com"},
        )
        issue = await api_client.get(
            f"/v1/leads/{create.json()['id']}/credential", headers=_auth(tenant_slug)
        )
        cred_token = issue.json()["credential"]

        # Flip a char in payload
        h, p, s = cred_token.split(".")
        tampered = f"{h}.{p[:-3]}XXX.{s}"

        resp = await api_client.post(
            "/v1/credentials/verify",
            headers=_auth(tenant_slug),
            json={"credential": tampered},
        )
        assert resp.json()["valid"] is False
