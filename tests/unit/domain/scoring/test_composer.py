"""Phase 2 — composer (formula audit layer) unit tests."""

from __future__ import annotations

from app.domain.scoring.composer import (
    DIVERGENCE_THRESHOLD,
    FIT_MAX,
    IDENTITY_MAX,
    INTENT_MAX,
    RISK_MIN_PENALTY,
    compose_pre_score,
    compute_median_score,
    is_divergent,
)
from app.domain.scoring.pre_score_judgment import (
    FitSignals,
    IdentitySignals,
    IntentSignals,
    PreScoreJudgmentResult,
    RiskSignals,
    SalesContext,
)


def _make_result(
    *,
    name_quality: str = "plausible",
    email_domain_class: str = "freemail",
    phone_validity: str = "valid_format",
    osint_digital_footprint: str = "low",
    project_specificity: str = "vague",
    budget_signal: str = "absent",
    timeline_signal: str = "absent",
    authority_signal: str = "absent",
    buying_stage: str = "curious_browsing",
    icp_alignment: str = "edge_case",
    project_type_in_tenant_scope: str = "unknown",
    geography_in_scope: str = "unknown",
    company_size_fit: str = "unknown",
    disposable_email: bool = False,
    suspicious_phone_pattern: bool = False,
    data_inconsistency_count: int = 0,
    spam_indicator_count: int = 0,
    competitor_mentioned: bool = False,
    direct_score: int = 50,
    extraction_confidence: float = 0.8,
) -> PreScoreJudgmentResult:
    return PreScoreJudgmentResult(
        thinking="x",
        identity=IdentitySignals(
            name_quality=name_quality,
            email_domain_class=email_domain_class,
            phone_validity=phone_validity,
            osint_digital_footprint=osint_digital_footprint,
        ),
        intent=IntentSignals(
            project_specificity=project_specificity,
            budget_signal=budget_signal,
            timeline_signal=timeline_signal,
            authority_signal=authority_signal,
            buying_stage=buying_stage,
        ),
        fit=FitSignals(
            icp_alignment=icp_alignment,
            project_type_in_tenant_scope=project_type_in_tenant_scope,
            geography_in_scope=geography_in_scope,
            company_size_fit=company_size_fit,
            segment_label="test",
        ),
        risk=RiskSignals(
            disposable_email=disposable_email,
            suspicious_phone_pattern=suspicious_phone_pattern,
            data_inconsistency_count=data_inconsistency_count,
            spam_indicator_count=spam_indicator_count,
            competitor_mentioned=competitor_mentioned,
        ),
        sales_context=SalesContext(
            who_they_are="x",
            company_or_buyer_profile="x",
            recommended_opening="x",
            key_questions_for_call=["a", "b", "c"],
        ),
        direct_score=direct_score,
        extraction_confidence=extraction_confidence,
    )


class TestComposePreScore:
    def test_ideal_corporate_lead_high_score(self):
        r = _make_result(
            name_quality="strong",
            email_domain_class="corporate_verified",
            phone_validity="valid_format",
            osint_digital_footprint="high",
            project_specificity="detailed",
            budget_signal="specific_amount",
            timeline_signal="committed_timeline",
            authority_signal="sole_decider",
            buying_stage="actively_evaluating",
            icp_alignment="ideal_match",
            project_type_in_tenant_scope="in_scope",
            geography_in_scope="core_market",
            company_size_fit="fit",
            extraction_confidence=0.9,
        )
        c = compose_pre_score(r)
        # strong(6) + corporate_verified(8) + valid_format(2) + high(3) = 19
        assert c.identity_component == 19
        assert c.intent_component > 30
        assert c.fit_component == FIT_MAX  # 20+6+5+4=35
        assert c.risk_penalty == 0
        assert c.total_audit >= 80

    def test_weak_anonymous_lead_low_score(self):
        r = _make_result(
            name_quality="weak",
            email_domain_class="freemail",
            project_specificity="vague",
            icp_alignment="edge_case",
            extraction_confidence=0.55,
        )
        c = compose_pre_score(r)
        assert c.total_audit < 40
        # confidence > 0.5, so no regression
        assert c.regressed_toward_mean is False

    def test_disposable_email_penalty(self):
        r = _make_result(
            disposable_email=True,
            suspicious_phone_pattern=True,
            extraction_confidence=0.8,
        )
        c = compose_pre_score(r)
        assert c.risk_penalty <= -15  # -10 disposable + -5 phone = -15 (clamped to -20)
        assert c.total_audit >= 0  # clamped at floor

    def test_score_clamped_to_100(self):
        r = _make_result(
            name_quality="strong",
            email_domain_class="corporate_verified",
            phone_validity="verified_reachable",
            osint_digital_footprint="high",
            project_specificity="detailed",
            budget_signal="specific_amount",
            timeline_signal="committed_timeline",
            authority_signal="sole_decider",
            buying_stage="ready_to_engage",
            icp_alignment="ideal_match",
            project_type_in_tenant_scope="in_scope",
            geography_in_scope="core_market",
            company_size_fit="fit",
            extraction_confidence=1.0,
        )
        c = compose_pre_score(r)
        assert 0 <= c.total_audit <= 100

    def test_score_clamped_to_0(self):
        r = _make_result(
            name_quality="random",
            email_domain_class="disposable",
            phone_validity="invalid_format",
            osint_digital_footprint="none",
            project_specificity="none",
            budget_signal="absent",
            timeline_signal="absent",
            authority_signal="absent",
            buying_stage="curious_browsing",
            icp_alignment="off_icp",
            project_type_in_tenant_scope="off_scope",
            geography_in_scope="outside",
            company_size_fit="too_small",
            disposable_email=True,
            suspicious_phone_pattern=True,
            data_inconsistency_count=5,
            spam_indicator_count=5,
            extraction_confidence=0.9,
        )
        c = compose_pre_score(r)
        assert 0 <= c.total_audit <= 100
        assert c.total_audit < 15  # all negatives

    def test_low_confidence_regresses_toward_mean(self):
        """Düşük extraction_confidence skoru 50'ye çeker."""
        r_low = _make_result(
            name_quality="random",  # would normally drive very low
            email_domain_class="disposable",
            disposable_email=True,
            extraction_confidence=0.2,  # very low
        )
        c_low = compose_pre_score(r_low)
        assert c_low.regressed_toward_mean is True
        # Raw would be very low (negative even); pulled toward 50
        # raw * 0.2 + 50 * 0.8 = 40 + raw*0.2; raw is clamped 0-90
        assert c_low.total_audit > 30  # dragged up from near-0

    def test_determinism(self):
        """Aynı input → aynı skor."""
        r = _make_result(extraction_confidence=0.75)
        scores = [compose_pre_score(r).total_audit for _ in range(10)]
        assert len(set(scores)) == 1

    def test_component_ranges(self):
        """Her bileşen kendi üst-alt sınırında kalır."""
        r = _make_result(
            name_quality="strong",
            email_domain_class="corporate_verified",
            phone_validity="verified_reachable",
            osint_digital_footprint="high",
            icp_alignment="ideal_match",
        )
        c = compose_pre_score(r)
        assert 0 <= c.identity_component <= IDENTITY_MAX
        assert 0 <= c.intent_component <= INTENT_MAX
        assert 0 <= c.fit_component <= FIT_MAX
        assert RISK_MIN_PENALTY <= c.risk_penalty <= 0


class TestComputeMedianScore:
    def test_three_scores_median(self):
        assert compute_median_score([30, 50, 70]) == 50

    def test_two_scores_average(self):
        # median of [30, 70] is 50
        assert compute_median_score([30, 70]) == 50

    def test_single_score(self):
        assert compute_median_score([42]) == 42

    def test_empty(self):
        assert compute_median_score([]) == 0

    def test_unsorted_input(self):
        assert compute_median_score([70, 30, 50]) == 50


class TestIsDivergent:
    def test_default_threshold(self):
        assert is_divergent(80, 50) is True  # diff 30 > 20
        assert is_divergent(55, 50) is False  # diff 5 < 20
        assert is_divergent(71, 50) is True  # diff 21 > 20
        assert is_divergent(70, 50) is False  # diff 20 == 20, not > 20

    def test_custom_threshold(self):
        assert is_divergent(60, 50, threshold=5) is True
        assert is_divergent(55, 50, threshold=10) is False

    def test_default_uses_module_constant(self):
        assert DIVERGENCE_THRESHOLD == 20
