"""Unit tests for webhook event filter wildcard matching."""

from __future__ import annotations

import pytest

from app.application.webhook.event_filter import matches, validate_patterns
from app.ports.event import EVENT_TYPES


class TestMatches:
    def test_star_matches_anything(self) -> None:
        assert matches("lead.won", ["*"]) is True
        assert matches("anything.at.all", ["*"]) is True

    def test_exact_match(self) -> None:
        assert matches("score.updated", ["score.updated"]) is True
        assert matches("score.updated", ["lead.won"]) is False

    def test_prefix_wildcard(self) -> None:
        assert matches("lead.won", ["lead.*"]) is True
        assert matches("lead.stage_changed", ["lead.*"]) is True
        assert matches("score.updated", ["lead.*"]) is False

    def test_prefix_does_not_match_unrelated_namespace(self) -> None:
        # "lead.*" must not match "leadx.something" — the dot is intentional.
        assert matches("leadx.thing", ["lead.*"]) is False

    def test_multiple_patterns_any_match(self) -> None:
        patterns = ["lead.*", "score.updated"]
        assert matches("lead.won", patterns) is True
        assert matches("score.updated", patterns) is True
        assert matches("pre_score.judged", patterns) is False

    def test_empty_patterns_match_nothing(self) -> None:
        assert matches("lead.won", []) is False

    def test_blanks_ignored(self) -> None:
        assert matches("lead.won", ["", "  ", "lead.won"]) is True


class TestValidatePatterns:
    def test_star_is_valid(self) -> None:
        assert validate_patterns(["*"], EVENT_TYPES) == []

    def test_known_exact_event_is_valid(self) -> None:
        assert validate_patterns(["lead.won"], EVENT_TYPES) == []

    def test_known_namespace_wildcard_is_valid(self) -> None:
        assert validate_patterns(["lead.*", "score.*"], EVENT_TYPES) == []

    def test_unknown_namespace_wildcard_is_invalid(self) -> None:
        bad = validate_patterns(["banana.*"], EVENT_TYPES)
        assert bad == ["banana.*"]

    def test_unknown_exact_event_is_invalid(self) -> None:
        bad = validate_patterns(["lead.not_a_thing"], EVENT_TYPES)
        assert bad == ["lead.not_a_thing"]

    def test_blank_is_invalid(self) -> None:
        bad = validate_patterns(["", "  "], EVENT_TYPES)
        # Two blank entries → both flagged.
        assert len(bad) == 2


@pytest.mark.parametrize(
    "event_type,patterns,expected",
    [
        ("lead.stage_changed", ["pipeline", "lead.*"], True),
        ("lead.stage_changed", ["score.*"], False),
        ("pre_score.judged", ["pre_score.judged"], True),
        ("pre_score.judged", ["*"], True),
    ],
)
def test_param_matrix(event_type: str, patterns: list[str], expected: bool) -> None:
    assert matches(event_type, patterns) is expected
