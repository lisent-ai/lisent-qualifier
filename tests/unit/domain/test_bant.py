"""Unit tests for BANTScore dataclass and Pydantic validation."""
import pytest
from pydantic import TypeAdapter, ValidationError
from app.domain.scoring.bant import BANTScore
from app.infrastructure.llm.schemas import BANTExtractionResult


class TestBANTScore:
    def test_total_sums_all_dimensions(self):
        bant = BANTScore(budget_score=20, authority_score=15, need_score=10, timeline_score=5)
        assert bant.total == 50

    def test_default_total_is_zero(self):
        assert BANTScore().total == 0

    def test_to_dict_includes_total(self):
        bant = BANTScore(budget_score=25, authority_score=25, need_score=25, timeline_score=25)
        d = bant.to_dict()
        assert d["total"] == 100


class TestBANTExtractionResultValidation:
    _adapter = TypeAdapter(BANTExtractionResult)

    def test_valid_json(self):
        raw = '{"budget_score":20,"authority_score":15,"need_score":10,"timeline_score":5,"confidence":"medium"}'
        result = self._adapter.validate_json(raw)
        assert result.total == 50
        assert result.confidence == "medium"

    def test_score_out_of_range_raises(self):
        raw = '{"budget_score":30,"authority_score":0,"need_score":0,"timeline_score":0}'
        with pytest.raises(ValidationError):
            self._adapter.validate_json(raw)

    def test_defaults_applied(self):
        raw = '{"budget_score":5,"authority_score":5,"need_score":5,"timeline_score":5}'
        result = self._adapter.validate_json(raw)
        assert result.confidence == "low"
        assert result.budget_notes == ""
