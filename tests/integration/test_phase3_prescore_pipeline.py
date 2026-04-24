"""Phase 3 — Pre-scoring pipeline end-to-end integration tests.

Covered:
    - Migration 004: qualifier_osint_profiles table RLS works
    - OSINTProfileRepository get_fresh/upsert roundtrip with RLS
    - POST /v1/leads enqueues to Redis (prescore:queue:{tid} + active_tenants)
    - PreScoreService scores a real DB lead end-to-end (mocked Groq)
    - DB writeback: score, score_breakdown.pre_score_ensemble, extra_data.osint
    - pre_score.judged ScoreEvent published

Requires:
    DATABASE_URL  (default host:5433 dev stack)
    REDIS_DSN     (default host:6380 dev stack)
    Migration 004 applied.

Groq + OSINT are mocked at the module level — no network traffic.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime

import pytest

try:
    import asyncpg
    import redis.asyncio as redis_async

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _HAS_DEPS, reason="asyncpg or redis missing"),
]

APP_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://app:qualifierpass@host.docker.internal:5433/lead_qualifier",
)
REDIS_DSN = os.getenv("REDIS_DSN", "redis://host.docker.internal:6380/0")


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
async def admin_conn():
    try:
        conn = await asyncpg.connect(APP_DB_URL)
    except Exception as exc:
        pytest.skip(f"DB unreachable: {exc}")
    try:
        has_osint = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='qualifier_osint_profiles')"
        )
        if not has_osint:
            pytest.skip("migration 004 not applied")
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def redis_client():
    try:
        r = redis_async.from_url(REDIS_DSN, decode_responses=False)
        await r.ping()
    except Exception as exc:
        pytest.skip(f"Redis unreachable: {exc}")
    yield r
    # Flush only prescore-related keys to not disturb other integration tests.
    async for key in r.scan_iter("prescore:*"):
        await r.delete(key)
    await r.aclose()


@pytest.fixture
async def prescore_tenant(admin_conn):
    """Fresh tenant for a prescore test; cleaned up on teardown."""

    async def _cleanup() -> None:
        await admin_conn.execute(
            "DELETE FROM qualifier_leads WHERE tenant_id IN "
            "(SELECT id FROM tenants WHERE slug = 'prescore-test')"
        )
        await admin_conn.execute(
            "DELETE FROM qualifier_osint_profiles WHERE tenant_id IN "
            "(SELECT id FROM tenants WHERE slug = 'prescore-test')"
        )
        await admin_conn.execute(
            "DELETE FROM tenants WHERE slug = 'prescore-test'"
        )

    await _cleanup()
    await admin_conn.execute(
        """
        INSERT INTO tenants (slug, name, source_type, plan, status, config)
        VALUES ('prescore-test', 'Prescore Test', 'standalone', 'pro', 'active',
                '{"qualification_threshold": 70}'::jsonb)
        """
    )
    row = await admin_conn.fetchrow(
        "SELECT id, slug FROM tenants WHERE slug = 'prescore-test'"
    )
    yield dict(row)
    await _cleanup()


# ============================================================================
# Migration 004 — direct DB sanity
# ============================================================================

class TestMigration004:
    async def test_table_exists_with_rls_forced(self, admin_conn):
        row = await admin_conn.fetchrow(
            """
            SELECT relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relname = 'qualifier_osint_profiles'
            """
        )
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True

    async def test_unique_tenant_key_hash(self, admin_conn, prescore_tenant):
        tid = prescore_tenant["id"]
        key_hash = hashlib.sha256(b"test@x.com|905551234567").hexdigest()

        await admin_conn.execute(
            """
            INSERT INTO qualifier_osint_profiles
                (tenant_id, key_hash, email, phone, profile, provider)
            VALUES ($1, $2, 'test@x.com', '+905551234567', '{}'::jsonb, 'test')
            """,
            tid, key_hash,
        )
        # Second insert with same (tenant_id, key_hash) must violate unique.
        with pytest.raises(asyncpg.UniqueViolationError):
            await admin_conn.execute(
                """
                INSERT INTO qualifier_osint_profiles
                    (tenant_id, key_hash, email, phone, profile, provider)
                VALUES ($1, $2, 'test@x.com', '+905551234567', '{}'::jsonb, 'test')
                """,
                tid, key_hash,
            )

    async def test_cascade_on_tenant_delete(self, admin_conn):
        """ON DELETE CASCADE: tenant silinince profilleri de silinir."""
        tid = uuid.uuid4()
        await admin_conn.execute(
            """
            INSERT INTO tenants (id, slug, name, source_type, plan, status)
            VALUES ($1, 'cascade-test', 'Cascade', 'standalone', 'pro', 'active')
            """,
            tid,
        )
        key_hash = hashlib.sha256(b"cascade-test").hexdigest()
        await admin_conn.execute(
            """
            INSERT INTO qualifier_osint_profiles
                (tenant_id, key_hash, profile, provider)
            VALUES ($1, $2, '{}'::jsonb, 'test')
            """,
            tid, key_hash,
        )
        await admin_conn.execute("DELETE FROM tenants WHERE id = $1", tid)
        count = await admin_conn.fetchval(
            "SELECT COUNT(*) FROM qualifier_osint_profiles WHERE tenant_id = $1",
            tid,
        )
        assert count == 0


# ============================================================================
# OSINTProfileRepository — real DB roundtrip
# ============================================================================

class TestOSINTProfileRepoRoundtrip:
    async def test_upsert_then_get_fresh(self, admin_conn, prescore_tenant):
        """RLS superuser bypass ile upsert + get_fresh roundtrip."""
        from app.infrastructure.db.osint_profile_repo import (
            get_fresh,
            make_key_hash,
            upsert,
        )
        from app.ports.osint import (
            OSINTEmailSignals,
            OSINTPhoneSignals,
            OSINTProfile,
        )

        tid = prescore_tenant["id"]
        # SET app.tenant_id for RLS policy
        await admin_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tid))

        # Fake Pool wrapper since get_fresh/upsert expect pool-like (fetchrow/execute)
        class _SingleConnPool:
            def __init__(self, c):
                self._c = c
            async def fetchrow(self, *a, **k):
                return await self._c.fetchrow(*a, **k)
            async def execute(self, *a, **k):
                return await self._c.execute(*a, **k)

        pool = _SingleConnPool(admin_conn)
        key_hash = make_key_hash("ayse@onurinsaat.com.tr", "+905551234567")
        profile = OSINTProfile(
            provider="self_hosted",
            fetched_at=datetime.now(UTC),
            phone=OSINTPhoneSignals(country="TR", country_code=90, valid=True),
            email=OSINTEmailSignals(
                domain="onurinsaat.com.tr", domain_type="corporate",
                registered_sites=["linkedin", "github"], site_count=2,
            ),
            notes=["phone TR", "email corporate"],
        )

        # Upsert (insert)
        await upsert(
            pool,
            tenant_id=tid, key_hash=key_hash,
            email="ayse@onurinsaat.com.tr", phone="+905551234567",
            profile=profile,
        )

        # Get_fresh returns equivalent OSINTProfile
        got = await get_fresh(pool, tenant_id=tid, key_hash=key_hash)
        assert got is not None
        assert got.provider == "self_hosted"
        assert got.phone.country == "TR"
        assert got.email.registered_sites == ["linkedin", "github"]

    async def test_second_upsert_increments_refetch_count(
        self, admin_conn, prescore_tenant,
    ):
        from app.infrastructure.db.osint_profile_repo import make_key_hash, upsert
        from app.ports.osint import OSINTProfile

        tid = prescore_tenant["id"]
        await admin_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tid))

        class _P:
            def __init__(self, c): self._c = c
            async def fetchrow(self, *a, **k): return await self._c.fetchrow(*a, **k)
            async def execute(self, *a, **k): return await self._c.execute(*a, **k)

        pool = _P(admin_conn)
        key_hash = make_key_hash("x@y.com", "+1234567890")
        p = OSINTProfile(provider="test", fetched_at=datetime.now(UTC))

        await upsert(pool, tenant_id=tid, key_hash=key_hash,
                     email="x@y.com", phone="+1234567890", profile=p)
        await upsert(pool, tenant_id=tid, key_hash=key_hash,
                     email="x@y.com", phone="+1234567890", profile=p)

        count = await admin_conn.fetchval(
            "SELECT refetch_count FROM qualifier_osint_profiles "
            "WHERE tenant_id=$1 AND key_hash=$2",
            tid, key_hash,
        )
        assert count == 1  # first insert = 0; second upsert → +1


# ============================================================================
# Queue enqueue — real Redis
# ============================================================================

class TestEnqueueReal:
    async def test_enqueue_populates_tenant_queue_and_active_set(
        self, redis_client, prescore_tenant,
    ):
        from app.application.prescore.config import (
            active_tenants_key,
            queue_key,
        )
        from app.application.prescore.queue import enqueue_lead

        tid = prescore_tenant["id"]
        lid = uuid.uuid4()
        await enqueue_lead(redis_client, tenant_id=tid, lead_id=lid)

        q_len = await redis_client.llen(queue_key(str(tid)))
        assert q_len == 1
        is_active = await redis_client.sismember(active_tenants_key(), str(tid))
        assert is_active

        raw = await redis_client.lindex(queue_key(str(tid)), 0)
        job = json.loads(raw)
        assert job["lead_id"] == str(lid)
        assert job["attempt"] == 0


# ============================================================================
# PreScoreService end-to-end (mocked Groq + real DB + real Redis)
# ============================================================================

class TestPreScoreServiceE2E:
    async def test_score_full_pipeline(
        self, admin_conn, redis_client, prescore_tenant, monkeypatch,
    ):
        """POST /v1/leads → enqueue → PreScoreService.score → DB updated + event."""
        from app.application.scoring.pre_score_ensemble import EnsembleResult
        from app.application.scoring.pre_score_service import PreScoreService
        from app.domain.scoring.pre_score_judgment import (
            FitSignals,
            IdentitySignals,
            IntentSignals,
            PreScoreJudgmentResult,
            RiskSignals,
            SalesContext,
        )
        from app.ports.event import ScoreEvent

        tid = prescore_tenant["id"]
        lead_id = uuid.uuid4()
        await admin_conn.execute(
            """
            INSERT INTO qualifier_leads
                (id, tenant_id, company_id, lead_id, phone, name, email, city,
                 source, project_type, budget_range, score, extra_data)
            VALUES ($1, $2, $2, 'e2e-1', '+905551234567', 'Ayşe Yılmaz',
                    'ayse@onurinsaat.com.tr', 'Ankara', 'web', 'commercial',
                    '10m_plus', 0, '{"notes": "Ankara plaza projesi"}'::jsonb)
            """,
            lead_id, tid,
        )

        # Mock the ensemble — return deterministic high-score result.
        def _canned() -> PreScoreJudgmentResult:
            return PreScoreJudgmentResult(
                thinking="...",
                identity=IdentitySignals(
                    name_quality="strong",
                    email_domain_class="corporate_verified",
                    phone_validity="valid_format",
                    osint_digital_footprint="medium",
                ),
                intent=IntentSignals(
                    project_specificity="detailed",
                    budget_signal="range_stated",
                    timeline_signal="committed_timeline",
                    authority_signal="sole_decider",
                    buying_stage="actively_evaluating",
                ),
                fit=FitSignals(
                    icp_alignment="ideal_match",
                    project_type_in_tenant_scope="in_scope",
                    geography_in_scope="core_market",
                    company_size_fit="fit",
                    segment_label="Kurumsal müteahhit",
                ),
                risk=RiskSignals(),
                sales_context=SalesContext(
                    who_they_are="Onur İnşaat PM",
                    company_or_buyer_profile="Ankara inşaat firması",
                    recommended_opening="Referans plazaları göster",
                    key_questions_for_call=["Timeline?", "Bütçe?", "Karar vericileri?"],
                ),
                direct_score=88,
                extraction_confidence=0.9,
            )

        async def fake_ensemble(**kwargs):
            agg = _canned()
            return EnsembleResult(
                median_direct_score=88,
                formula_audit_score=82,
                divergent=False,
                divergence_abs=6,
                extraction_confidence=0.9,
                aggregated_signals=agg,
                raw_personas={"skeptic": _canned(), "neutral": _canned(), "opportunity": _canned()},
                persona_scores={"skeptic": 85, "neutral": 88, "opportunity": 92},
                elapsed_ms=2200,
            )

        monkeypatch.setattr(
            "app.application.scoring.pre_score_service.run_pre_score_ensemble",
            fake_ensemble,
        )

        # Fake EventPort capturing published events
        captured: list[ScoreEvent] = []

        class _Capture:
            async def publish(self, event):
                captured.append(event)

            async def subscribe(self, *a, **k):
                raise NotImplementedError

        from app.adapters.osint import StubOSINTAdapter

        service = PreScoreService(osint=StubOSINTAdapter(), event_port=_Capture())

        await admin_conn.execute(
            "SELECT set_config('app.tenant_id', $1, false)", str(tid),
        )
        outcome = await service.score(
            tenant_id=tid,
            lead_id=lead_id,
            conn=admin_conn,
            ideal_customer_profile="Türkiye inşaat firmaları",
            sector="construction",
            qualification_threshold=70,
        )

        assert outcome.final_score == 88
        assert not outcome.used_fallback
        assert outcome.divergent is False

        # DB state: lead.score updated + score_breakdown populated
        row = await admin_conn.fetchrow(
            "SELECT score, score_breakdown, extra_data FROM qualifier_leads "
            "WHERE id = $1",
            lead_id,
        )
        assert row["score"] == 88
        breakdown = json.loads(row["score_breakdown"])
        assert breakdown["fit_score"] == 88
        assert "pre_score_ensemble" in breakdown
        assert breakdown["pre_score_ensemble"]["median_direct_score"] == 88
        assert breakdown["pre_score_ensemble"]["divergent"] is False

        raw_extra = row["extra_data"]
        extra_data = json.loads(raw_extra) if isinstance(raw_extra, str) else raw_extra
        assert "osint" in extra_data
        assert extra_data["osint"]["provider"] == "stub"

        # Event published with correct event_type + payload
        assert len(captured) == 1
        ev = captured[0]
        assert ev.event_type == "pre_score.judged"
        assert ev.score == 88
        assert ev.threshold == 70
        assert ev.path == "fast"  # 88 >= 70
        assert ev.payload["ensemble"]["median_direct_score"] == 88
        assert "sales_context" in ev.payload["ensemble"]

    async def test_fallback_when_ensemble_fails(
        self, admin_conn, prescore_tenant, monkeypatch,
    ):
        """3 persona all fail → fallback score ≤ 45 written to DB."""
        from app.adapters.osint import StubOSINTAdapter
        from app.application.scoring.pre_score_service import PreScoreService

        tid = prescore_tenant["id"]
        lead_id = uuid.uuid4()
        await admin_conn.execute(
            """
            INSERT INTO qualifier_leads
                (id, tenant_id, company_id, lead_id, phone, name, email, city,
                 source, project_type, budget_range, score, extra_data)
            VALUES ($1, $2, $2, 'fallback-1', '+905551234567', 'Test',
                    'test@example.com', 'X', 'web', '', '', 0,
                    '{"notes": "some notes that are at least twenty chars"}'::jsonb)
            """,
            lead_id, tid,
        )

        async def failing_ensemble(**kwargs):
            raise RuntimeError("all personas dead")

        monkeypatch.setattr(
            "app.application.scoring.pre_score_service.run_pre_score_ensemble",
            failing_ensemble,
        )

        class _NoopEvents:
            async def publish(self, event): pass
            async def subscribe(self, *a, **k): raise NotImplementedError

        service = PreScoreService(osint=StubOSINTAdapter(), event_port=_NoopEvents())

        await admin_conn.execute(
            "SELECT set_config('app.tenant_id', $1, false)", str(tid),
        )
        outcome = await service.score(
            tenant_id=tid, lead_id=lead_id, conn=admin_conn,
            qualification_threshold=70,
        )

        assert outcome.used_fallback
        assert outcome.final_score <= 45

        row = await admin_conn.fetchrow(
            "SELECT score, score_breakdown FROM qualifier_leads WHERE id = $1",
            lead_id,
        )
        assert row["score"] <= 45
        breakdown = json.loads(row["score_breakdown"])
        assert "pre_score_fallback" in breakdown
        assert breakdown["pre_score_fallback"]["reason"] == "ensemble_all_failed"


# ============================================================================
# Phase 7 — Legacy /webhook/lead/{token} thin intake
# ============================================================================

class TestLegacyWebhookPhase7:
    """Intake handler after Phase 7 rewire: dedup only, no scoring."""

    async def test_handler_dedup_accepts_new_lead(self, redis_client):
        from app.application.lead_intake.commands import ProcessWebhookLeadCommand
        from app.application.lead_intake.handler import ProcessWebhookLeadHandler
        from app.infrastructure.redis.score_repo import ScoreRepository
        from app.infrastructure.redis.session_repo import SessionRepository

        # Clean slate
        async for key in redis_client.scan_iter("dedup:*"):
            await redis_client.delete(key)

        session_repo = SessionRepository(redis_client, ttl=3600)
        score_repo = ScoreRepository(redis_client)
        handler = ProcessWebhookLeadHandler(session_repo, score_repo)

        cmd = ProcessWebhookLeadCommand(
            lead_data={"lead_id": "phase7-dedup-new-1", "name": "X"},
            fallback_url=None,
            company_id="test-co",
            external_lead_id=None,
        )
        result = await handler.handle(cmd)
        assert result == {"status": "accepted", "lead_id": "phase7-dedup-new-1"}

    async def test_handler_dedup_rejects_repeat(self, redis_client):
        from app.application.lead_intake.commands import ProcessWebhookLeadCommand
        from app.application.lead_intake.handler import ProcessWebhookLeadHandler
        from app.infrastructure.redis.score_repo import ScoreRepository
        from app.infrastructure.redis.session_repo import SessionRepository

        async for key in redis_client.scan_iter("dedup:*"):
            await redis_client.delete(key)

        handler = ProcessWebhookLeadHandler(
            SessionRepository(redis_client, ttl=3600),
            ScoreRepository(redis_client),
        )
        cmd = ProcessWebhookLeadCommand(
            lead_data={"lead_id": "phase7-dedup-repeat-1", "name": "X"},
            fallback_url=None,
            company_id="test-co",
            external_lead_id=None,
        )
        first = await handler.handle(cmd)
        second = await handler.handle(cmd)
        assert first["status"] == "accepted"
        assert second["status"] == "duplicate"

    async def test_handler_prefers_external_lead_id_for_dedup(self, redis_client):
        from app.application.lead_intake.commands import ProcessWebhookLeadCommand
        from app.application.lead_intake.handler import ProcessWebhookLeadHandler
        from app.infrastructure.redis.score_repo import ScoreRepository
        from app.infrastructure.redis.session_repo import SessionRepository

        async for key in redis_client.scan_iter("dedup:*"):
            await redis_client.delete(key)

        handler = ProcessWebhookLeadHandler(
            SessionRepository(redis_client, ttl=3600),
            ScoreRepository(redis_client),
        )
        # Same external_lead_id but different sender-side lead_id — should dedup
        cmd_a = ProcessWebhookLeadCommand(
            lead_data={"lead_id": "sender-1", "name": "X"},
            fallback_url=None,
            company_id="test-co",
            external_lead_id="crm-row-42",
        )
        cmd_b = ProcessWebhookLeadCommand(
            lead_data={"lead_id": "sender-2", "name": "Y"},
            fallback_url=None,
            company_id="test-co",
            external_lead_id="crm-row-42",  # same CRM row
        )
        first = await handler.handle(cmd_a)
        second = await handler.handle(cmd_b)
        assert first["status"] == "accepted"
        assert second["status"] == "duplicate"  # dedup on external_lead_id

