"""Unit tests for QualificationJudgment dataclass and backward compatibility."""
import pytest
from pydantic import TypeAdapter, ValidationError

from app.domain.scoring.champ import CHAMPScore
from app.domain.scoring.qualification_judgment import QualificationJudgment
from app.infrastructure.llm.schemas import QualificationJudgmentResult


class TestQualificationJudgment:
    def test_total_sums_all_dimensions(self):
        j = QualificationJudgment(
            challenges_score=20, authority_score=15, money_score=10, prioritization_score=5,
        )
        assert j.total == 50

    def test_default_total_is_zero(self):
        assert QualificationJudgment().total == 0

    def test_avg_confidence(self):
        j = QualificationJudgment(
            challenges_confidence=0.8,
            authority_confidence=0.6,
            money_confidence=0.4,
            prioritization_confidence=0.2,
        )
        assert j.avg_confidence == pytest.approx(0.5)

    def test_biggest_gap_returns_lowest_dimension(self):
        j = QualificationJudgment(
            challenges_score=20, authority_score=5, money_score=15, prioritization_score=10,
        )
        assert j.biggest_gap() == "authority"

    def test_filled_dimensions(self):
        j = QualificationJudgment(challenges_score=10, money_score=5)
        assert j.filled_dimensions() == 2

    def test_merge_monotonic_keeps_higher_scores(self):
        old = QualificationJudgment(
            challenges_score=20, authority_score=10, money_score=15, prioritization_score=18,
            holistic_score=60,
        )
        new = QualificationJudgment(
            challenges_score=15, authority_score=20, money_score=12, prioritization_score=22,
            holistic_score=70,
        )
        merged = old.merge_monotonic(new)
        assert merged.challenges_score == 20   # kept old
        assert merged.authority_score == 20    # took new
        assert merged.money_score == 15        # kept old
        assert merged.prioritization_score == 22  # took new
        assert merged.holistic_score == 70     # took max

    def test_merge_monotonic_holistic_never_decreases(self):
        old = QualificationJudgment(holistic_score=80)
        new = QualificationJudgment(holistic_score=60)
        merged = old.merge_monotonic(new)
        assert merged.holistic_score == 80

    def test_merge_monotonic_unions_negative_signals(self):
        old = QualificationJudgment(negative_signals=["price_fishing"])
        new = QualificationJudgment(negative_signals=["just_looking"])
        merged = old.merge_monotonic(new)
        assert set(merged.negative_signals) == {"price_fishing", "just_looking"}

    def test_merge_monotonic_takes_more_severe_penalty(self):
        old = QualificationJudgment(negative_penalty=-10)
        new = QualificationJudgment(negative_penalty=-30)
        merged = old.merge_monotonic(new)
        assert merged.negative_penalty == -30

    def test_merge_monotonic_sector_qualifiers_prefers_newer(self):
        old = QualificationJudgment(sector_qualifiers={"has_land": True, "permit_status": None})
        new = QualificationJudgment(sector_qualifiers={"has_land": None, "permit_status": "approved"})
        merged = old.merge_monotonic(new)
        assert merged.sector_qualifiers["has_land"] is True  # kept old (newer is None)
        assert merged.sector_qualifiers["permit_status"] == "approved"  # took newer

    def test_merge_monotonic_updates_reasoning_from_newer(self):
        old = QualificationJudgment(challenges_reasoning="old reason")
        new = QualificationJudgment(challenges_reasoning="new reason")
        merged = old.merge_monotonic(new)
        assert merged.challenges_reasoning == "new reason"


class TestBackwardCompatibility:
    """Ensure QualificationJudgment is compatible with CHAMPScore."""

    def test_to_champ_dict_includes_all_champ_fields(self):
        j = QualificationJudgment(
            challenges_score=20, authority_score=15, money_score=22, prioritization_score=18,
            challenges_reasoning="test note",
            challenges_confidence=0.8,
            extraction_version=3,
        )
        d = j.to_champ_dict()
        # All CHAMPScore.to_dict() fields must be present
        assert d["challenges_score"] == 20
        assert d["authority_score"] == 15
        assert d["money_score"] == 22
        assert d["prioritization_score"] == 18
        assert d["total"] == 75
        assert d["challenges_notes"] == "test note"  # mapped from reasoning
        assert d["challenges_confidence"] == 0.8
        assert d["extraction_version"] == 3

    def test_champ_score_from_judgment_dict(self):
        """CHAMPScore.from_dict() should work with a judgment dict (ignoring extra fields)."""
        j = QualificationJudgment(
            challenges_score=20, authority_score=15, money_score=22, prioritization_score=18,
            holistic_score=85,  # extra field — should be ignored by CHAMPScore
            negative_signals=["price_fishing"],
            challenges_reasoning="evidence here",
            extraction_version=2,
        )
        d = j.to_champ_dict()
        champ = CHAMPScore.from_dict(d)
        assert champ.total == 75
        assert champ.challenges_notes == "evidence here"
        assert champ.extraction_version == 2

    def test_to_champ_score_conversion(self):
        j = QualificationJudgment(
            challenges_score=20, authority_score=15, money_score=22, prioritization_score=18,
            challenges_reasoning="note",
            challenges_confidence=0.8,
            extraction_version=3,
        )
        champ = j.to_champ_score()
        assert isinstance(champ, CHAMPScore)
        assert champ.total == 75
        assert champ.challenges_notes == "note"
        assert champ.extraction_version == 3

    def test_from_dict_round_trip(self):
        original = QualificationJudgment(
            challenges_score=20, authority_score=15, money_score=22, prioritization_score=18,
            holistic_score=85,
            holistic_reasoning="Strong lead",
            icp_fit_assessment="Good match",
            negative_signals=["price_fishing"],
            negative_penalty=-10,
            sector_qualifiers={"has_land": True, "permit_status": "approved"},
            recommended_next_question="Ask about budget",
            extraction_version=3,
            scoring_mode="llm_judge",
        )
        rebuilt = QualificationJudgment.from_dict(original.to_champ_dict())
        assert rebuilt.holistic_score == 85
        assert rebuilt.holistic_reasoning == "Strong lead"
        assert rebuilt.negative_signals == ["price_fishing"]
        assert rebuilt.negative_penalty == -10
        assert rebuilt.sector_qualifiers["has_land"] is True
        assert rebuilt.extraction_version == 3
        assert rebuilt.scoring_mode == "llm_judge"

    def test_from_dict_reads_champ_dict(self):
        """QualificationJudgment.from_dict() should handle a plain CHAMPScore dict."""
        champ = CHAMPScore(
            challenges_score=20, authority_score=15,
            challenges_notes="from champ",
            extraction_version=2,
        )
        j = QualificationJudgment.from_dict(champ.to_dict())
        assert j.challenges_score == 20
        assert j.challenges_reasoning == "from champ"
        assert j.holistic_score == 0  # not present in CHAMP dict
        assert j.extraction_version == 2


class TestQualificationJudgmentResult:
    """Test the Pydantic schema for LLM output parsing."""

    _adapter = TypeAdapter(QualificationJudgmentResult)

    def test_valid_minimal_json(self):
        raw = (
            '{"thinking":"test","challenges_score":20,"authority_score":15,'
            '"money_score":10,"prioritization_score":5,"holistic_score":60,'
            '"confidence":"medium"}'
        )
        result = self._adapter.validate_json(raw)
        assert result.total == 50
        assert result.holistic_score == 60

    def test_score_out_of_range_raises(self):
        raw = (
            '{"thinking":"","challenges_score":30,"authority_score":0,'
            '"money_score":0,"prioritization_score":0,"holistic_score":50}'
        )
        with pytest.raises(ValidationError):
            self._adapter.validate_json(raw)

    def test_holistic_out_of_range_raises(self):
        raw = (
            '{"thinking":"","challenges_score":0,"authority_score":0,'
            '"money_score":0,"prioritization_score":0,"holistic_score":150}'
        )
        with pytest.raises(ValidationError):
            self._adapter.validate_json(raw)

    def test_negative_penalty_must_be_non_positive(self):
        raw = (
            '{"thinking":"","challenges_score":0,"authority_score":0,'
            '"money_score":0,"prioritization_score":0,"holistic_score":50,'
            '"negative_penalty":10}'
        )
        with pytest.raises(ValidationError):
            self._adapter.validate_json(raw)

    def test_defaults_applied(self):
        raw = (
            '{"thinking":"","challenges_score":5,"authority_score":5,'
            '"money_score":5,"prioritization_score":5,"holistic_score":30}'
        )
        result = self._adapter.validate_json(raw)
        assert result.confidence == "low"
        assert result.negative_signals == []
        assert result.missing_info == []
        assert result.recommended_next_question == ""

    def test_from_judgment_result_factory(self):
        result = QualificationJudgmentResult(
            thinking="step by step",
            challenges_score=20,
            challenges_reasoning="clear project",
            challenges_confidence=0.9,
            authority_score=15,
            authority_reasoning="joint decision",
            authority_confidence=0.7,
            money_score=22,
            money_reasoning="budget stated",
            money_confidence=0.85,
            prioritization_score=18,
            prioritization_reasoning="6 months",
            prioritization_confidence=0.7,
            holistic_score=78,
            holistic_reasoning="Good lead",
            icp_fit_assessment="Partial match",
            negative_signals=[],
            negative_penalty=0,
            recommended_next_question="Ask about land",
            confidence="medium",
        )
        j = QualificationJudgment.from_judgment_result(result, extraction_version=2)
        assert j.challenges_score == 20
        assert j.holistic_score == 78
        assert j.thinking == "step by step"
        assert j.extraction_version == 2
        assert j.scoring_mode == "llm_judge"
