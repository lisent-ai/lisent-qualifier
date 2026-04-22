"""
Phase 2.C — Glass Box CHAMP score response integration tests.

Covered:
    - GET /v1/leads/{id}/score — default lead (status='new', score=0) → valid shape
    - CHAMP structure: 4 dimensions with score/confidence/evidence/missing
    - Components: fit_score, qualification_score, engagement_score, sector_bonus
    - Recommendation: next_action + suggested_question (gap-based)
    - CHAMP fed from qualifier_sessions.champ_json — full data
    - score_breakdown fed from qualifier_leads.score_breakdown — full data
    - Cross-tenant 404
"""

from __future__ import annotations

import json
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
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def tenant(admin_conn):
    await admin_conn.execute(
        """
        DELETE FROM qualifier_sessions WHERE lead_id IN (
            SELECT id FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug='glassbox-test'
            )
        );
        DELETE FROM qualifier_leads WHERE tenant_id IN (SELECT id FROM tenants WHERE slug='glassbox-test');
        DELETE FROM tenants WHERE slug='glassbox-test';
        """
    )
    await admin_conn.execute(
        """
        INSERT INTO tenants (slug, name, source_type, plan, status, config)
        VALUES ('glassbox-test', 'Glass Box Test', 'standalone', 'pro', 'active',
                '{"qualification_threshold": 70}'::jsonb)
        """
    )
    row = await admin_conn.fetchrow("SELECT id, slug FROM tenants WHERE slug='glassbox-test'")
    yield dict(row)
    await admin_conn.execute(
        """
        DELETE FROM qualifier_sessions WHERE lead_id IN (
            SELECT id FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug='glassbox-test'
            )
        );
        DELETE FROM qualifier_leads WHERE tenant_id IN (SELECT id FROM tenants WHERE slug='glassbox-test');
        DELETE FROM tenants WHERE slug='glassbox-test';
        """
    )


@pytest.fixture
async def api_client():
    from app.main import app as _app

    async with _app.router.lifespan_context(_app):
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _auth(slug: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {slug}"}


class TestDefaultLeadScore:
    async def test_default_lead_returns_valid_glass_box_shape(self, api_client, tenant):
        create = await api_client.post(
            "/v1/leads", headers=_auth(tenant["slug"]), json={"name": "Test"}
        )
        assert create.status_code == 201
        lead_id = create.json()["id"]

        resp = await api_client.get(
            f"/v1/leads/{lead_id}/score", headers=_auth(tenant["slug"])
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Top-level
        assert body["lead_id"] == lead_id
        assert body["tenant_id"] == str(tenant["id"])
        assert body["framework"] == "champ"
        assert body["score"] == 0
        assert body["threshold"] == 70  # from tenant.config
        assert body["status"] == "new"

        # Glass Box structure
        exp = body["explanation"]
        assert "components" in exp
        assert "champ" in exp
        assert "recommendation" in exp

        # CHAMP 4 dimensions
        champ = exp["champ"]
        for dim in ("challenges", "authority", "money", "prioritization"):
            assert dim in champ
            d = champ[dim]
            assert d["score"] == 0
            assert d["max"] == 25
            assert d["confidence"] == 0.0
            assert d["evidence"] == []

        # Recommendation (default — no score → ask_question + low priority)
        rec = exp["recommendation"]
        assert rec["next_action"] in {"ask_question", "schedule_call", "engage_more"}
        assert rec["priority"] in {"high", "medium", "low"}


class TestPopulatedCHAMP:
    async def test_lead_with_champ_session_returns_evidence(self, api_client, tenant, admin_conn):
        # Create lead
        create = await api_client.post(
            "/v1/leads", headers=_auth(tenant["slug"]), json={"name": "Ali", "external_ref": "cb-1"}
        )
        lead_id = create.json()["id"]

        # Inject session with CHAMP extraction
        champ_data = {
            "challenges_score": 18,
            "authority_score": 12,
            "money_score": 22,
            "prioritization_score": 17,
            "challenges_confidence": 0.85,
            "authority_confidence": 0.45,
            "money_confidence": 0.90,
            "prioritization_confidence": 0.75,
            "challenges_notes": "aileye yer yaratmak istiyor\n3+1 daire arıyor",
            "authority_notes": "eşiyle birlikte karar verecek",
            "money_notes": "3 milyon TL bütçe\nbankayla ön görüşme yaptı",
            "prioritization_notes": "6 ay içinde başlamak istiyor",
            "extraction_version": 2,
            "missing_info": ["authority: sole decision maker unclear"],
        }
        # Also update lead score + breakdown
        score_breakdown = {
            "fit_score": 65,
            "qualification_score": 69,
            "engagement_score": 80,
            "sector_bonus": 5,
        }
        await admin_conn.execute(
            """
            UPDATE qualifier_leads
            SET score = 72, status = 'qualifying',
                score_breakdown = $2::jsonb
            WHERE id = $1
            """,
            uuid.UUID(lead_id),
            json.dumps(score_breakdown),
        )
        await admin_conn.execute(
            """
            INSERT INTO qualifier_sessions (lead_id, company_id, tenant_id, score, stage, champ_json, messages)
            VALUES ($1, $2, $2, 72, 'chat', $3::jsonb, '[]'::jsonb)
            """,
            uuid.UUID(lead_id),
            tenant["id"],
            json.dumps(champ_data),
        )

        resp = await api_client.get(
            f"/v1/leads/{lead_id}/score", headers=_auth(tenant["slug"])
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["score"] == 72
        assert body["status"] == "qualifying"

        # CHAMP populated
        champ = body["explanation"]["champ"]
        assert champ["challenges"]["score"] == 18
        assert champ["challenges"]["confidence"] == 0.85
        assert "aileye yer yaratmak istiyor" in champ["challenges"]["evidence"][0]
        assert champ["authority"]["score"] == 12
        assert champ["money"]["confidence"] == 0.90
        assert champ["extraction_version"] == 2

        # Missing info parses
        assert any("authority" in m.lower() for m in champ["authority"]["missing"])

        # Components populated
        comps = body["explanation"]["components"]
        assert comps["fit_score"]["value"] == 65
        assert comps["fit_score"]["weight"] == 0.30
        assert comps["qualification_score"]["value"] == 69
        assert comps["qualification_score"]["weight"] == 0.45


class TestIsolation:
    async def test_cross_tenant_score_returns_404(self, api_client, tenant, admin_conn):
        # Lead yarat
        create = await api_client.post(
            "/v1/leads", headers=_auth(tenant["slug"]), json={"name": "X"}
        )
        lead_id = create.json()["id"]

        # Başka tenant yarat
        await admin_conn.execute(
            """
            DELETE FROM tenants WHERE slug='glassbox-other';
            INSERT INTO tenants (slug, name, source_type, plan, status)
            VALUES ('glassbox-other', 'Other', 'standalone', 'pro', 'active')
            """
        )
        try:
            resp = await api_client.get(
                f"/v1/leads/{lead_id}/score", headers=_auth("glassbox-other")
            )
            assert resp.status_code == 404
        finally:
            await admin_conn.execute("DELETE FROM tenants WHERE slug='glassbox-other'")


class TestRecommendation:
    async def test_default_low_score_suggests_question(self, api_client, tenant):
        create = await api_client.post(
            "/v1/leads", headers=_auth(tenant["slug"]), json={"name": "Low"}
        )
        resp = await api_client.get(
            f"/v1/leads/{create.json()['id']}/score", headers=_auth(tenant["slug"])
        )
        rec = resp.json()["explanation"]["recommendation"]
        # Score 0 < 70 threshold → ask_question path
        assert rec["next_action"] == "ask_question"
        assert rec["suggested_question"]  # not None
