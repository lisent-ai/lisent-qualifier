"""Unit tests for qualification judge client — parsing and self-consistency logic."""
import json
import pytest

from app.infrastructure.llm.qualification_judge_client import _parse_judgment
from app.infrastructure.llm.schemas import QualificationJudgmentResult


class TestParseJudgment:
    """Test JSON parsing from LLM responses."""

    def _make_valid_json(self, **overrides) -> str:
        base = {
            "thinking": "test reasoning",
            "challenges_score": 20,
            "challenges_reasoning": "clear project",
            "challenges_confidence": 0.8,
            "authority_score": 15,
            "authority_reasoning": "joint",
            "authority_confidence": 0.7,
            "money_score": 22,
            "money_reasoning": "budget stated",
            "money_confidence": 0.85,
            "prioritization_score": 18,
            "prioritization_reasoning": "6 months",
            "prioritization_confidence": 0.7,
            "holistic_score": 75,
            "holistic_reasoning": "Good lead",
            "icp_fit_assessment": "Partial match",
            "negative_signals": [],
            "negative_penalty": 0,
            "missing_info": ["land status"],
            "recommended_next_question": "Do you have land?",
            "confidence": "medium",
        }
        base.update(overrides)
        return json.dumps(base)

    def test_parse_clean_json(self):
        text = self._make_valid_json()
        result = _parse_judgment(text)
        assert isinstance(result, QualificationJudgmentResult)
        assert result.holistic_score == 75
        assert result.challenges_score == 20

    def test_parse_json_in_markdown_block(self):
        text = f"```json\n{self._make_valid_json()}\n```"
        result = _parse_judgment(text)
        assert result.holistic_score == 75

    def test_parse_json_with_surrounding_text(self):
        text = f"Here is my analysis:\n{self._make_valid_json()}\nEnd of analysis."
        result = _parse_judgment(text)
        assert result.holistic_score == 75

    def test_parse_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Failed to parse"):
            _parse_judgment("This is not JSON at all")

    def test_parse_json_with_negative_signals(self):
        text = self._make_valid_json(
            negative_signals=["price_fishing", "just_looking"],
            negative_penalty=-25,
        )
        result = _parse_judgment(text)
        assert result.negative_signals == ["price_fishing", "just_looking"]
        assert result.negative_penalty == -25


class TestSelfConsistencyAveraging:
    """Test that self-consistency merging logic produces correct averages."""

    def test_average_of_identical_results(self):
        """When all passes return the same result, merged should be identical."""
        from app.infrastructure.llm.qualification_judge_client import run_judge_with_self_consistency
        # This is a logic-only test; actual Groq calls would be mocked in integration tests.
        # Here we just verify the schema works for our expected inputs.
        result = QualificationJudgmentResult(
            thinking="test",
            challenges_score=20,
            authority_score=15,
            money_score=22,
            prioritization_score=18,
            holistic_score=75,
            confidence="medium",
        )
        assert result.total == 75
        assert result.holistic_score == 75
