"""
Few-shot example registry.

Returns the appropriate examples based on language + sector combination.
"""
from __future__ import annotations


class FewShotRegistry:
    """Returns few-shot examples formatted as a prompt string."""

    @staticmethod
    def get_examples(language: str, sector: str) -> str:
        lang = language.lower().strip()
        sec = sector.lower().strip()

        if lang in ("tr", "turkish") and sec in ("construction", "insaat", "inşaat"):
            from app.domain.conversation.few_shots.tr_construction import format_few_shot_examples
            return format_few_shot_examples()

        if lang in ("en", "english") and sec in ("construction",):
            from app.domain.conversation.few_shots.en_construction import format_few_shot_examples
            return format_few_shot_examples()

        # No examples available for this combination
        return ""

    @staticmethod
    def get_judge_examples(language: str, sector: str) -> str:
        """Returns few-shot examples for the qualification judge."""
        lang = language.lower().strip()
        sec = sector.lower().strip()

        if lang in ("tr", "turkish") and sec in ("construction", "insaat", "inşaat"):
            from app.domain.conversation.few_shots.tr_construction_judge import format_judge_few_shot_examples
            return format_judge_few_shot_examples()

        if lang in ("en", "english") and sec in ("construction",):
            from app.domain.conversation.few_shots.en_construction_judge import format_judge_few_shot_examples
            return format_judge_few_shot_examples()

        return ""
