"""Phase 3 — PreScoreService orchestrator unit tests.

Mock ensemble + OSINT + DB + EventPort → verify:
    - Happy path: ensemble score written + event published
    - Ensemble total failure → fallback score ≤ 45
    - OSINT failure → stub profile, ensemble still runs
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

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
from app.ports.osint import OSINTError, OSINTPort, OSINTProfile

pytestmark = pytest.mark.asyncio


# ============================================================================
# Test doubles
# ============================================================================

class _StubOSINT(OSINTPort):
    def __init__(self, raise_exc: Exception | None = None):
        self._raise = raise_exc

    @property
    def name(self) -> str:
        return "stub_test"

    async def enrich(self, *, email, phone, name=None, tenant_id=None):
        if self._raise is not None:
            raise self._raise
        return OSINTProfile(
            provider="stub_test",
            fetched_at=datetime.now(UTC),
            notes=["test osint"],
        )


@dataclass
class _FakeEventPort:
    published: list[ScoreEvent] = field(default_factory=list)

    async def publish(self, event: ScoreEvent) -> None:
        self.published.append(event)

    async def subscribe(self, tenant_id, session_id=None):
        raise NotImplementedError


class _FakeConn:
    def __init__(self, lead_row: dict[str, Any] | None):
        self._lead_row = lead_row
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, query: str, *args):
        if self._lead_row is None:
            return None
        return self._lead_row

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        return "UPDATE 1"


def _minimal_result(direct_score: int = 50) -> PreScoreJudgmentResult:
    return PreScoreJudgmentResult(
        thinking="x",
        identity=IdentitySignals(
            name_quality="plausible",
            email_domain_class="freemail",
            phone_validity="valid_format",
            osint_digital_footprint="low",
        ),
        intent=IntentSignals(
            project_specificity="vague",
            budget_signal="absent",
            timeline_signal="absent",
            authority_signal="absent",
            buying_stage="curious_browsing",
        ),
        fit=FitSignals(
            icp_alignment="edge_case",
            project_type_in_tenant_scope="unknown",
            geography_in_scope="unknown",
            company_size_fit="unknown",
            segment_label="test",
        ),
        risk=RiskSignals(),
        sales_context=SalesContext(
            who_they_are="x",
            company_or_buyer_profile="x",
            recommended_opening="x",
            key_questions_for_call=["a", "b", "c"],
        ),
        direct_score=direct_score,
        extraction_confidence=0.8,
    )


# ============================================================================
# Tests
# ============================================================================

class TestPreScoreServiceScore:
    async def test_happy_path_writes_score_and_publishes_event(self, monkeypatch):
        lead_id = uuid4()
        tenant_id = uuid4()
        conn = _FakeConn({
            "id": str(lead_id),
            "tenant_id": str(tenant_id),
            "name": "Test User",
            "email": "test@example.com",
            "phone": "+905551234567",
            "city": "Istanbul",
            "source": "web",
            "project_type": "residential",
            "budget_range": "1m_3m",
            "extra_data": {"notes": "Looking for an apartment in Istanbul"},
            "raw_payload": {},
        })
        event_port = _FakeEventPort()
        service = PreScoreService(
            osint=_StubOSINT(),
            event_port=event_port,
        )

        # Mock ensemble at module boundary
        from app.application.scoring.pre_score_ensemble import EnsembleResult

        async def fake_ensemble(*, lead_json, osint_json, **kwargs):
            agg = _minimal_result(direct_score=42)
            return EnsembleResult(
                median_direct_score=42,
                formula_audit_score=35,
                divergent=False,
                divergence_abs=7,
                extraction_confidence=0.8,
                aggregated_signals=agg,
                raw_personas={
                    "skeptic": _minimal_result(40),
                    "neutral": _minimal_result(42),
                    "opportunity": _minimal_result(45),
                },
                persona_scores={"skeptic": 40, "neutral": 42, "opportunity": 45},
                elapsed_ms=2500,
            )

        monkeypatch.setattr(
            "app.application.scoring.pre_score_service.run_pre_score_ensemble",
            fake_ensemble,
        )

        outcome = await service.score(
            tenant_id=tenant_id,
            lead_id=lead_id,
            conn=conn,
            qualification_threshold=75,
        )

        assert outcome.final_score == 42
        assert not outcome.used_fallback
        assert outcome.osint_provider == "stub_test"

        # DB UPDATE was called with correct final_score
        assert len(conn.executed) == 1
        update_query, update_args = conn.executed[0]
        assert "UPDATE qualifier_leads" in update_query
        assert update_args[0] == 42  # final_score

        # breakdown JSONB
        breakdown = json.loads(update_args[1])
        assert breakdown["fit_score"] == 42
        assert "pre_score_ensemble" in breakdown

        # ScoreEvent published
        assert len(event_port.published) == 1
        event = event_port.published[0]
        assert event.event_type == "pre_score.judged"
        assert event.score == 42
        assert event.path == "chat"  # 42 < 75 threshold
        assert event.payload["ensemble"]["median_direct_score"] == 42

    async def test_fallback_when_ensemble_totally_fails(self, monkeypatch):
        """On the final worker attempt, ensemble failure collapses to the
        data-quality fallback so the lead row is unlocked rather than left
        looping in the retry queue forever."""
        lead_id = uuid4()
        tenant_id = uuid4()
        conn = _FakeConn({
            "id": str(lead_id),
            "tenant_id": str(tenant_id),
            "name": "Test",
            "email": "test@example.com",
            "phone": "+905551234567",
            "city": "Istanbul",
            "source": "web",
            "project_type": "",
            "budget_range": "",
            "extra_data": {"notes": "long enough notes to count for fallback"},
            "raw_payload": {},
        })
        event_port = _FakeEventPort()
        service = PreScoreService(osint=_StubOSINT(), event_port=event_port)

        async def failing_ensemble(**kwargs):
            raise RuntimeError("all 3 personas dead")

        monkeypatch.setattr(
            "app.application.scoring.pre_score_service.run_pre_score_ensemble",
            failing_ensemble,
        )

        outcome = await service.score(
            tenant_id=tenant_id, lead_id=lead_id, conn=conn,
            is_final_attempt=True,
        )

        assert outcome.used_fallback is True
        assert outcome.final_score <= 45  # fallback max
        assert outcome.fallback_result is not None
        assert outcome.ensemble_result is None

        # DB still updated
        assert len(conn.executed) == 1
        # Event still published
        assert len(event_port.published) == 1
        assert event_port.published[0].payload["fallback"]["reason"] == "ensemble_all_failed"

    async def test_raises_on_early_attempt_ensemble_failure(self, monkeypatch):
        """Before the final attempt, ensemble failure propagates so the
        worker can schedule a retry with backoff — covering bursty rate
        limit windows that recover within a minute."""
        lead_id = uuid4()
        tenant_id = uuid4()
        conn = _FakeConn({
            "id": str(lead_id), "tenant_id": str(tenant_id),
            "name": "Test", "email": "t@x.com", "phone": "+905551234567",
            "city": "", "source": "", "project_type": "", "budget_range": "",
            "extra_data": {}, "raw_payload": {},
        })
        event_port = _FakeEventPort()
        service = PreScoreService(osint=_StubOSINT(), event_port=event_port)

        async def failing_ensemble(**kwargs):
            raise RuntimeError("groq rate limit")

        monkeypatch.setattr(
            "app.application.scoring.pre_score_service.run_pre_score_ensemble",
            failing_ensemble,
        )

        import pytest as _pytest
        with _pytest.raises(RuntimeError, match="groq rate limit"):
            await service.score(
                tenant_id=tenant_id, lead_id=lead_id, conn=conn,
                attempt=0, is_final_attempt=False,
            )

        # No DB writeback, no event published — worker will retry.
        assert len(conn.executed) == 0
        assert len(event_port.published) == 0

    async def test_osint_failure_ensemble_still_runs(self, monkeypatch):
        lead_id = uuid4()
        tenant_id = uuid4()
        conn = _FakeConn({
            "id": str(lead_id), "tenant_id": str(tenant_id),
            "name": "X", "email": "x@y.com", "phone": "+905551234567",
            "city": "", "source": "", "project_type": "", "budget_range": "",
            "extra_data": {}, "raw_payload": {},
        })
        event_port = _FakeEventPort()
        service = PreScoreService(
            osint=_StubOSINT(raise_exc=OSINTError("upstream dead")),
            event_port=event_port,
        )

        from app.application.scoring.pre_score_ensemble import EnsembleResult

        captured_osint = {}

        async def fake_ensemble(*, lead_json, osint_json, **kwargs):
            captured_osint["provider"] = osint_json.get("provider")
            agg = _minimal_result(direct_score=30)
            return EnsembleResult(
                median_direct_score=30, formula_audit_score=25,
                divergent=False, divergence_abs=5, extraction_confidence=0.6,
                aggregated_signals=agg,
                raw_personas={"neutral": agg},
                persona_scores={"neutral": 30},
                elapsed_ms=1000,
            )

        monkeypatch.setattr(
            "app.application.scoring.pre_score_service.run_pre_score_ensemble",
            fake_ensemble,
        )

        outcome = await service.score(
            tenant_id=tenant_id, lead_id=lead_id, conn=conn,
        )
        assert outcome.final_score == 30
        assert outcome.osint_provider == "stub_osint_failure"
        assert captured_osint["provider"] == "stub_osint_failure"

    async def test_lead_not_found_raises(self):
        service = PreScoreService(osint=_StubOSINT(), event_port=_FakeEventPort())
        conn = _FakeConn(None)
        with pytest.raises(ValueError, match="Lead not found"):
            await service.score(
                tenant_id=uuid4(), lead_id=uuid4(), conn=conn,
            )

    async def test_threshold_determines_path(self, monkeypatch):
        lead_id = uuid4()
        tenant_id = uuid4()
        conn = _FakeConn({
            "id": str(lead_id), "tenant_id": str(tenant_id),
            "name": "X", "email": "x@y.com", "phone": "+905551234567",
            "city": "", "source": "", "project_type": "", "budget_range": "",
            "extra_data": {}, "raw_payload": {},
        })
        event_port = _FakeEventPort()
        service = PreScoreService(osint=_StubOSINT(), event_port=event_port)

        from app.application.scoring.pre_score_ensemble import EnsembleResult

        async def fake_ensemble(**kwargs):
            agg = _minimal_result(direct_score=90)
            return EnsembleResult(
                median_direct_score=90, formula_audit_score=85,
                divergent=False, divergence_abs=5, extraction_confidence=0.9,
                aggregated_signals=agg,
                raw_personas={"neutral": agg},
                persona_scores={"neutral": 90}, elapsed_ms=2000,
            )

        monkeypatch.setattr(
            "app.application.scoring.pre_score_service.run_pre_score_ensemble",
            fake_ensemble,
        )

        await service.score(
            tenant_id=tenant_id, lead_id=lead_id, conn=conn,
            qualification_threshold=75,
        )
        assert event_port.published[0].path == "fast"  # 90 >= 75

    async def test_event_publish_failure_does_not_block(self, monkeypatch):
        """Event port fail etse skorlama işi tamamlanmış olsun."""
        class _ExplodingEvents:
            async def publish(self, event):
                raise RuntimeError("event bus down")
            async def subscribe(self, *args, **kwargs):
                raise NotImplementedError

        lead_id = uuid4()
        tenant_id = uuid4()
        conn = _FakeConn({
            "id": str(lead_id), "tenant_id": str(tenant_id),
            "name": "X", "email": "x@y.com", "phone": "+905551234567",
            "city": "", "source": "", "project_type": "", "budget_range": "",
            "extra_data": {}, "raw_payload": {},
        })
        service = PreScoreService(osint=_StubOSINT(), event_port=_ExplodingEvents())

        from app.application.scoring.pre_score_ensemble import EnsembleResult

        async def fake_ensemble(**kwargs):
            agg = _minimal_result(direct_score=55)
            return EnsembleResult(
                median_direct_score=55, formula_audit_score=50,
                divergent=False, divergence_abs=5, extraction_confidence=0.7,
                aggregated_signals=agg, raw_personas={"neutral": agg},
                persona_scores={"neutral": 55}, elapsed_ms=1500,
            )

        monkeypatch.setattr(
            "app.application.scoring.pre_score_service.run_pre_score_ensemble",
            fake_ensemble,
        )

        # Should complete without raising
        outcome = await service.score(
            tenant_id=tenant_id, lead_id=lead_id, conn=conn,
        )
        assert outcome.final_score == 55
