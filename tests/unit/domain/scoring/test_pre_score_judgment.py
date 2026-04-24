"""Phase 2 — PreScoreJudgmentResult schema validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.scoring.pre_score_judgment import (
    FitSignals,
    IdentitySignals,
    IntentSignals,
    PreScoreJudgmentResult,
    RiskSignals,
    SalesContext,
)


def _minimal_kwargs() -> dict:
    return dict(
        thinking="x",
        identity=IdentitySignals(
            name_quality="weak",
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
            segment_label="x",
        ),
        risk=RiskSignals(),
        sales_context=SalesContext(
            who_they_are="x",
            company_or_buyer_profile="x",
            recommended_opening="x",
            key_questions_for_call=["a", "b", "c"],
        ),
        direct_score=50,
        extraction_confidence=0.5,
    )


class TestPreScoreJudgmentResult:
    def test_minimal_valid(self):
        r = PreScoreJudgmentResult(**_minimal_kwargs())
        assert r.direct_score == 50
        assert r.extraction_confidence == 0.5

    def test_direct_score_range(self):
        kwargs = _minimal_kwargs()
        kwargs["direct_score"] = -1
        with pytest.raises(ValidationError):
            PreScoreJudgmentResult(**kwargs)
        kwargs["direct_score"] = 101
        with pytest.raises(ValidationError):
            PreScoreJudgmentResult(**kwargs)

    def test_confidence_range(self):
        kwargs = _minimal_kwargs()
        kwargs["extraction_confidence"] = 1.1
        with pytest.raises(ValidationError):
            PreScoreJudgmentResult(**kwargs)
        kwargs["extraction_confidence"] = -0.1
        with pytest.raises(ValidationError):
            PreScoreJudgmentResult(**kwargs)

    def test_invalid_enum_rejected(self):
        with pytest.raises(ValidationError):
            IdentitySignals(
                name_quality="brilliant",  # not in Literal
                email_domain_class="freemail",
                phone_validity="valid_format",
                osint_digital_footprint="low",
            )

    def test_key_questions_min_length(self):
        with pytest.raises(ValidationError):
            SalesContext(
                who_they_are="x",
                company_or_buyer_profile="x",
                recommended_opening="x",
                key_questions_for_call=["a", "b"],  # < 3
            )

    def test_key_questions_max_length(self):
        with pytest.raises(ValidationError):
            SalesContext(
                who_they_are="x",
                company_or_buyer_profile="x",
                recommended_opening="x",
                key_questions_for_call=["a", "b", "c", "d", "e", "f"],  # > 5
            )

    def test_json_roundtrip(self):
        r = PreScoreJudgmentResult(**_minimal_kwargs())
        dumped = r.model_dump()
        restored = PreScoreJudgmentResult.model_validate(dumped)
        assert restored == r

    def test_evidence_max_length(self):
        """Her signal group'ta evidence max (identity=6, intent=6, fit=6, risk=5) limitler."""
        with pytest.raises(ValidationError):
            IdentitySignals(
                name_quality="weak",
                email_domain_class="freemail",
                phone_validity="valid_format",
                osint_digital_footprint="low",
                evidence=["e"] * 7,  # > 6
            )
