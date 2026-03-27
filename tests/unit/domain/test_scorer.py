"""Unit tests for RuleBasedScorer — boundary conditions and routing logic."""
import pytest
from app.domain.lead.entities import Lead, ContactInfo
from app.domain.lead.enums import (
    LeadSource, ProjectType, BudgetRange, DecisionAuthority, TimelineUrgency,
)
from app.domain.scoring.scorer import RuleBasedScorer
from app.domain.scoring.thresholds import HIGH_THRESHOLD


@pytest.fixture
def scorer() -> RuleBasedScorer:
    return RuleBasedScorer()


def make_lead(**kwargs) -> Lead:
    defaults = dict(
        id="test-lead-1",
        source=LeadSource.INSTAGRAM,
        contact=ContactInfo(name="Ali Veli", phone="05001234567"),
        project_type=ProjectType.RESIDENTIAL,
        budget_range=BudgetRange.RANGE_1M_3M,
        decision_authority=DecisionAuthority.SOLE,
        timeline_urgency=TimelineUrgency.SHORT,
    )
    defaults.update(kwargs)
    return Lead(**defaults)


class TestBudgetScoring:
    def test_over_10m_gets_max_budget_score(self, scorer):
        lead = make_lead(budget_range=BudgetRange.OVER_10M)
        assert scorer.breakdown(lead)["budget"] == 30

    def test_unknown_budget_gets_zero(self, scorer):
        lead = make_lead(budget_range=BudgetRange.UNKNOWN)
        assert scorer.breakdown(lead)["budget"] == 0

    def test_under_500k_gets_minimum(self, scorer):
        lead = make_lead(budget_range=BudgetRange.UNDER_500K)
        assert scorer.breakdown(lead)["budget"] == 4


class TestTimelineScoring:
    def test_immediate_urgency_max_score(self, scorer):
        lead = make_lead(timeline_urgency=TimelineUrgency.IMMEDIATE)
        assert scorer.breakdown(lead)["timeline"] == 25

    def test_unknown_timeline_zero(self, scorer):
        lead = make_lead(timeline_urgency=TimelineUrgency.UNKNOWN)
        assert scorer.breakdown(lead)["timeline"] == 0


class TestProjectTypeScoring:
    def test_commercial_max_score(self, scorer):
        lead = make_lead(project_type=ProjectType.COMMERCIAL)
        assert scorer.breakdown(lead)["project_type"] == 20

    def test_industrial_max_score(self, scorer):
        lead = make_lead(project_type=ProjectType.INDUSTRIAL)
        assert scorer.breakdown(lead)["project_type"] == 20


class TestDataQuality:
    def test_complete_data_gets_quality_bonus(self, scorer):
        lead = make_lead(
            contact=ContactInfo(
                name="Ali Veli", phone="05001234567",
                email="ali@example.com", city="Istanbul"
            ),
            budget_amount=2_000_000,
            notes="İki katlı villa inşaatı planlanıyor, ruhsat hazır.",
        )
        quality = scorer.breakdown(lead)["data_quality"]
        assert quality == 10  # email(3) + city(2) + budget_amount(3) + long_notes(2)

    def test_minimal_data_zero_quality(self, scorer):
        lead = make_lead()
        quality = scorer.breakdown(lead)["data_quality"]
        assert quality == 0


class TestRoutingThreshold:
    def test_top_tier_lead_above_threshold(self, scorer):
        lead = make_lead(
            project_type=ProjectType.COMMERCIAL,
            budget_range=BudgetRange.OVER_10M,
            decision_authority=DecisionAuthority.SOLE,
            timeline_urgency=TimelineUrgency.IMMEDIATE,
            contact=ContactInfo(
                name="Ali", phone="0500", email="a@b.com", city="Istanbul"
            ),
            budget_amount=15_000_000,
            notes="Büyük ticari proje, hemen başlamak istiyoruz.",
        )
        score = scorer.compute(lead)
        assert score >= HIGH_THRESHOLD

    def test_poor_lead_below_threshold(self, scorer):
        lead = make_lead(
            project_type=ProjectType.OTHER,
            budget_range=BudgetRange.UNKNOWN,
            decision_authority=DecisionAuthority.UNKNOWN,
            timeline_urgency=TimelineUrgency.UNKNOWN,
        )
        score = scorer.compute(lead)
        assert score < HIGH_THRESHOLD

    def test_score_clamped_to_100(self, scorer):
        lead = make_lead(
            project_type=ProjectType.COMMERCIAL,
            budget_range=BudgetRange.OVER_10M,
            decision_authority=DecisionAuthority.SOLE,
            timeline_urgency=TimelineUrgency.IMMEDIATE,
            contact=ContactInfo(
                name="Ali", phone="0500", email="a@b.com", city="Istanbul"
            ),
            budget_amount=20_000_000,
            notes="Çok büyük endüstriyel proje acilen başlatılacak.",
        )
        score = scorer.compute(lead)
        assert 0 <= score <= 100

    def test_score_never_negative(self, scorer):
        lead = make_lead(
            budget_range=BudgetRange.UNKNOWN,
            decision_authority=DecisionAuthority.UNKNOWN,
            timeline_urgency=TimelineUrgency.UNKNOWN,
        )
        assert scorer.compute(lead) >= 0


class TestBreakdownSumsToTotal:
    def test_breakdown_total_matches_compute(self, scorer):
        lead = make_lead()
        bd = scorer.breakdown(lead)
        expected = bd["budget"] + bd["timeline"] + bd["project_type"] + bd["authority"] + bd["data_quality"]
        assert bd["total"] == min(100, max(0, expected))
        assert bd["total"] == scorer.compute(lead)
