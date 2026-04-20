"""
Few-shot example registry.

Returns the appropriate examples based on language + sector combination.
"""
from __future__ import annotations


_REAL_ESTATE_SECTORS = ("real_estate", "real-estate", "realestate", "emlak", "gayrimenkul")
_CONSTRUCTION_SECTORS = ("construction", "insaat", "inşaat")


class FewShotRegistry:
    """Returns few-shot examples formatted as a prompt string."""

    @staticmethod
    def get_examples(language: str, sector: str) -> str:
        lang = language.lower().strip()
        sec = sector.lower().strip()

        if lang in ("tr", "turkish"):
            if sec in _REAL_ESTATE_SECTORS:
                from app.domain.conversation.few_shots.tr_real_estate import format_few_shot_examples
                return format_few_shot_examples()
            if sec in _CONSTRUCTION_SECTORS:
                from app.domain.conversation.few_shots.tr_construction import format_few_shot_examples
                return format_few_shot_examples()

        if lang in ("en", "english"):
            if sec in _REAL_ESTATE_SECTORS:
                from app.domain.conversation.few_shots.en_real_estate import format_few_shot_examples
                return format_few_shot_examples()
            if sec in _CONSTRUCTION_SECTORS:
                from app.domain.conversation.few_shots.en_construction import format_few_shot_examples
                return format_few_shot_examples()

        # No examples available for this combination
        return ""

    @staticmethod
    def get_judge_examples(language: str, sector: str) -> str:
        """Returns few-shot examples for the qualification judge."""
        lang = language.lower().strip()
        sec = sector.lower().strip()

        if lang in ("tr", "turkish"):
            if sec in _REAL_ESTATE_SECTORS:
                from app.domain.conversation.few_shots.tr_real_estate_judge import format_judge_few_shot_examples
                return format_judge_few_shot_examples()
            if sec in _CONSTRUCTION_SECTORS:
                from app.domain.conversation.few_shots.tr_construction_judge import format_judge_few_shot_examples
                return format_judge_few_shot_examples()

        if lang in ("en", "english"):
            if sec in _REAL_ESTATE_SECTORS:
                from app.domain.conversation.few_shots.en_real_estate_judge import format_judge_few_shot_examples
                return format_judge_few_shot_examples()
            if sec in _CONSTRUCTION_SECTORS:
                from app.domain.conversation.few_shots.en_construction_judge import format_judge_few_shot_examples
                return format_judge_few_shot_examples()

        return ""
