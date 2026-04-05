"""Unit tests for CHAMPScore dataclass and Pydantic validation."""
import pytest
from pydantic import TypeAdapter, ValidationError
from app.domain.scoring.champ import CHAMPScore
from app.infrastructure.llm.schemas import CHAMPExtractionResult


class TestCHAMPScore:
    def test_total_sums_all_dimensions(self):
        champ = CHAMPScore(
            challenges_score=20, authority_score=15, money_score=10, prioritization_score=5
        )
        assert champ.total == 50

    def test_default_total_is_zero(self):
        assert CHAMPScore().total == 0

    def test_to_dict_includes_total(self):
        champ = CHAMPScore(
            challenges_score=25, authority_score=25, money_score=25, prioritization_score=25
        )
        d = champ.to_dict()
        assert d["total"] == 100

    def test_biggest_gap_returns_lowest_dimension(self):
        champ = CHAMPScore(
            challenges_score=20, authority_score=5, money_score=15, prioritization_score=10
        )
        assert champ.biggest_gap() == "authority"

    def test_filled_dimensions(self):
        champ = CHAMPScore(challenges_score=10, money_score=5)
        assert champ.filled_dimensions() == 2

    def test_avg_confidence(self):
        champ = CHAMPScore(
            challenges_confidence=0.8,
            authority_confidence=0.6,
            money_confidence=0.4,
            prioritization_confidence=0.2,
        )
        assert champ.avg_confidence == pytest.approx(0.5)

    def test_merge_monotonic_keeps_higher_scores(self):
        old = CHAMPScore(challenges_score=20, authority_score=10, money_score=15, prioritization_score=18)
        new = CHAMPScore(challenges_score=15, authority_score=20, money_score=12, prioritization_score=22)
        merged = old.merge_monotonic(new)
        assert merged.challenges_score == 20  # kept old
        assert merged.authority_score == 20   # took new
        assert merged.money_score == 15       # kept old
        assert merged.prioritization_score == 22  # took new

    def test_merge_monotonic_updates_notes_from_newer(self):
        old = CHAMPScore(challenges_notes="old note")
        new = CHAMPScore(challenges_notes="new note")
        merged = old.merge_monotonic(new)
        assert merged.challenges_notes == "new note"

    def test_from_dict_round_trip(self):
        original = CHAMPScore(
            challenges_score=20,
            authority_score=15,
            money_score=22,
            prioritization_score=18,
            challenges_confidence=0.8,
            extraction_version=3,
        )
        rebuilt = CHAMPScore.from_dict(original.to_dict())
        assert rebuilt.total == original.total
        assert rebuilt.challenges_confidence == 0.8
        assert rebuilt.extraction_version == 3


class TestCHAMPExtractionResultValidation:
    _adapter = TypeAdapter(CHAMPExtractionResult)

    def test_valid_json(self):
        raw = '{"challenges_score":20,"authority_score":15,"money_score":10,"prioritization_score":5,"confidence":"medium"}'
        result = self._adapter.validate_json(raw)
        assert result.total == 50
        assert result.confidence == "medium"

    def test_score_out_of_range_raises(self):
        raw = '{"challenges_score":30,"authority_score":0,"money_score":0,"prioritization_score":0}'
        with pytest.raises(ValidationError):
            self._adapter.validate_json(raw)

    def test_defaults_applied(self):
        raw = '{"challenges_score":5,"authority_score":5,"money_score":5,"prioritization_score":5}'
        result = self._adapter.validate_json(raw)
        assert result.confidence == "low"
        assert result.challenges_notes == ""

    def test_confidence_scores_validated(self):
        raw = '{"challenges_score":10,"authority_score":10,"money_score":10,"prioritization_score":10,"challenges_confidence":0.9,"authority_confidence":0.7,"money_confidence":0.5,"prioritization_confidence":0.3}'
        result = self._adapter.validate_json(raw)
        assert result.challenges_confidence == 0.9
        assert result.prioritization_confidence == 0.3
