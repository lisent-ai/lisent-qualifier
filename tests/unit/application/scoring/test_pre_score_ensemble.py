"""Phase 2 — PreScoreEnsemble aggregation tests.

Pattern: mock `run_pre_score_persona` ile 3 canned persona çıktısı ver,
aggregation + formula audit + divergence behavior'ı doğrula.
"""

from __future__ import annotations

import pytest

from app.application.scoring import pre_score_ensemble as ens
from app.domain.scoring.pre_score_judgment import (
    FitSignals,
    IdentitySignals,
    IntentSignals,
    PreScoreJudgmentResult,
    RiskSignals,
    SalesContext,
)


def _result(
    *,
    direct_score: int = 50,
    confidence: float = 0.8,
    name_quality: str = "plausible",
    email_domain: str = "freemail",
    icp: str = "edge_case",
    segment: str = "unknown",
    evidence_identity: list[str] | None = None,
    disposable: bool = False,
) -> PreScoreJudgmentResult:
    return PreScoreJudgmentResult(
        thinking="x",
        identity=IdentitySignals(
            name_quality=name_quality,
            email_domain_class=email_domain,
            phone_validity="valid_format",
            osint_digital_footprint="low",
            evidence=evidence_identity or [],
        ),
        intent=IntentSignals(
            project_specificity="vague",
            budget_signal="absent",
            timeline_signal="absent",
            authority_signal="absent",
            buying_stage="curious_browsing",
        ),
        fit=FitSignals(
            icp_alignment=icp,
            project_type_in_tenant_scope="unknown",
            geography_in_scope="unknown",
            company_size_fit="unknown",
            segment_label=segment,
        ),
        risk=RiskSignals(disposable_email=disposable),
        sales_context=SalesContext(
            who_they_are="x",
            company_or_buyer_profile="x",
            recommended_opening="x",
            key_questions_for_call=["a", "b", "c"],
        ),
        direct_score=direct_score,
        extraction_confidence=confidence,
    )


class TestMajorityVoteEnum:
    def test_two_of_three_wins(self):
        disagreements: list[str] = []
        v = ens._majority_vote_enum(["freemail", "freemail", "corporate_verified"],
                                     disagreements, "test")
        assert v == "freemail"
        assert disagreements == []

    def test_three_of_three_wins(self):
        disagreements: list[str] = []
        v = ens._majority_vote_enum(["strong", "strong", "strong"],
                                     disagreements, "test")
        assert v == "strong"
        assert disagreements == []

    def test_all_different_uses_first_and_logs(self):
        disagreements: list[str] = []
        v = ens._majority_vote_enum(["a", "b", "c"], disagreements, "my.field")
        assert v == "a"  # first = skeptic, as tie-breaker
        assert len(disagreements) == 1
        assert "my.field" in disagreements[0]


class TestAggregateSignals:
    def test_all_same_signals_no_disagreements(self):
        personas = {
            "skeptic": _result(direct_score=30),
            "neutral": _result(direct_score=35),
            "opportunity": _result(direct_score=45),
        }
        agg, disagreements = ens._aggregate_signals(personas)
        assert disagreements == []
        assert agg.direct_score == 35  # median
        assert agg.identity.name_quality == "plausible"

    def test_two_one_split_takes_majority(self):
        personas = {
            "skeptic": _result(email_domain="freemail"),
            "neutral": _result(email_domain="freemail"),
            "opportunity": _result(email_domain="corporate_suspected"),
        }
        agg, _ = ens._aggregate_signals(personas)
        assert agg.identity.email_domain_class == "freemail"

    def test_all_different_logs_disagreement(self):
        personas = {
            "skeptic": _result(icp="off_icp"),
            "neutral": _result(icp="partial_match"),
            "opportunity": _result(icp="ideal_match"),
        }
        _, disagreements = ens._aggregate_signals(personas)
        assert any("fit.icp_alignment" in d for d in disagreements)

    def test_majority_vote_bool_at_least_two(self):
        personas = {
            "skeptic": _result(disposable=True),
            "neutral": _result(disposable=True),
            "opportunity": _result(disposable=False),
        }
        agg, _ = ens._aggregate_signals(personas)
        assert agg.risk.disposable_email is True

    def test_evidence_union_dedups(self):
        personas = {
            "skeptic": _result(evidence_identity=["A", "B"]),
            "neutral": _result(evidence_identity=["B", "C"]),
            "opportunity": _result(evidence_identity=["C", "D"]),
        }
        agg, _ = ens._aggregate_signals(personas)
        assert set(agg.identity.evidence) == {"A", "B", "C", "D"}

    def test_segment_label_from_highest_confidence(self):
        personas = {
            "skeptic": _result(segment="skeptic_label", confidence=0.3),
            "neutral": _result(segment="neutral_label", confidence=0.9),
            "opportunity": _result(segment="opp_label", confidence=0.5),
        }
        agg, _ = ens._aggregate_signals(personas)
        assert agg.fit.segment_label == "neutral_label"

    def test_extraction_confidence_is_mean(self):
        personas = {
            "skeptic": _result(confidence=0.4),
            "neutral": _result(confidence=0.6),
            "opportunity": _result(confidence=0.8),
        }
        agg, _ = ens._aggregate_signals(personas)
        assert agg.extraction_confidence == pytest.approx(0.6, abs=0.01)


class TestRunPreScoreEnsemble:
    @pytest.mark.asyncio
    async def test_happy_path_three_successful(self, monkeypatch):
        call_count = {"n": 0}
        personas_seen: list[str] = []

        async def fake_run_persona(*, persona, lead_json, osint_json,
                                    ideal_customer_profile, sector,
                                    model_override=None, max_tokens_override=None,
                                    timeout_override=None):
            call_count["n"] += 1
            personas_seen.append(persona)
            return _result(direct_score={"skeptic": 30, "neutral": 35, "opportunity": 45}[persona])

        monkeypatch.setattr(
            "app.application.scoring.pre_score_ensemble.run_pre_score_persona",
            fake_run_persona,
        )

        result = await ens.run_pre_score_ensemble(
            lead_json={"email": "x@y.com"},
            osint_json={"provider": "stub"},
        )

        assert call_count["n"] == 3
        assert set(personas_seen) == {"skeptic", "neutral", "opportunity"}
        assert result.median_direct_score == 35  # median of 30,35,45
        assert result.formula_audit_score >= 0
        assert len(result.failures) == 0
        assert len(result.raw_personas) == 3
        assert result.persona_scores == {"skeptic": 30, "neutral": 35, "opportunity": 45}

    @pytest.mark.asyncio
    async def test_one_persona_fails_ensemble_continues(self, monkeypatch):
        async def fake_run_persona(*, persona, **kwargs):
            if persona == "skeptic":
                raise RuntimeError("simulated groq 500")
            return _result(direct_score=50)

        monkeypatch.setattr(
            "app.application.scoring.pre_score_ensemble.run_pre_score_persona",
            fake_run_persona,
        )

        result = await ens.run_pre_score_ensemble(
            lead_json={"email": "x@y.com"},
            osint_json={"provider": "stub"},
        )
        assert len(result.failures) == 1
        assert result.failures[0].persona == "skeptic"
        assert result.median_direct_score == 50  # median of [50,50]
        assert len(result.raw_personas) == 2

    @pytest.mark.asyncio
    async def test_all_personas_fail_raises(self, monkeypatch):
        async def fake_run_persona(**kwargs):
            raise RuntimeError("everyone dies")

        monkeypatch.setattr(
            "app.application.scoring.pre_score_ensemble.run_pre_score_persona",
            fake_run_persona,
        )
        with pytest.raises(RuntimeError, match="All .* personas failed"):
            await ens.run_pre_score_ensemble(
                lead_json={}, osint_json={},
            )

    @pytest.mark.asyncio
    async def test_divergence_flag(self, monkeypatch):
        """LLM median very different from formula → divergent=True."""
        async def fake_run_persona(*, persona, **kwargs):
            # Give LLM median=85 but signals all weak → formula would be low
            return _result(
                direct_score=85,
                name_quality="weak",        # formula low
                email_domain="freemail",
                icp="edge_case",
                confidence=0.8,
            )

        monkeypatch.setattr(
            "app.application.scoring.pre_score_ensemble.run_pre_score_persona",
            fake_run_persona,
        )

        result = await ens.run_pre_score_ensemble(
            lead_json={}, osint_json={},
            divergence_threshold=10,
        )
        assert result.median_direct_score == 85
        assert result.formula_audit_score < 50
        assert result.divergent is True
        assert result.divergence_abs > 10

    @pytest.mark.asyncio
    async def test_jsonb_breakdown_structure(self, monkeypatch):
        async def fake_run_persona(*, persona, **kwargs):
            return _result(direct_score=60)

        monkeypatch.setattr(
            "app.application.scoring.pre_score_ensemble.run_pre_score_persona",
            fake_run_persona,
        )

        result = await ens.run_pre_score_ensemble(lead_json={}, osint_json={})
        breakdown = result.to_jsonb_breakdown()
        assert "median_direct_score" in breakdown
        assert "formula_audit_score" in breakdown
        assert "divergent" in breakdown
        assert "persona_scores" in breakdown
        assert "raw_personas" in breakdown
        assert "aggregated_signals" in breakdown
        # Raw personas should have the 3 keys
        assert set(breakdown["raw_personas"].keys()) == {"skeptic", "neutral", "opportunity"}
