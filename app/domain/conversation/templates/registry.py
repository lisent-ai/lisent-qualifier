"""
Template registry — returns the correct prompt templates for a given language.

Usage:
    templates = TemplateRegistry.get_templates("tr")
    prompt = templates.chat_system
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTemplateSet:
    chat_system: str
    extraction: str
    extraction_sector_instruction: str
    reasoning: str
    closing: str
    qualification_judge: str = ""


class TemplateRegistry:
    """Returns the prompt template set for a given language code."""

    @staticmethod
    def get_templates(language: str, sector: str = "general") -> PromptTemplateSet:
        lang = language.lower().strip()

        if lang in ("tr", "turkish"):
            return _get_tr_templates(sector)
        if lang in ("en", "english"):
            return _get_en_templates(sector)

        # Fallback to English
        return _get_en_templates(sector)


def _get_tr_templates(sector: str) -> PromptTemplateSet:
    from app.domain.conversation.templates.tr.chat_system import CHAT_SYSTEM_TEMPLATE
    from app.domain.conversation.templates.tr.extraction import (
        CHAMP_EXTRACTION_TEMPLATE,
        CONSTRUCTION_QUALIFIERS_INSTRUCTION,
        GENERAL_QUALIFIERS_INSTRUCTION,
    )
    from app.domain.conversation.templates.tr.reasoning import REASONING_REPORT_TEMPLATE
    from app.domain.conversation.templates.tr.closing import CLOSING_TEMPLATE
    from app.domain.conversation.templates.tr.qualification_judge import QUALIFICATION_JUDGE_TEMPLATE

    sec = sector.lower().strip()
    sector_instr = GENERAL_QUALIFIERS_INSTRUCTION
    if sec in ("construction", "insaat", "inşaat"):
        sector_instr = CONSTRUCTION_QUALIFIERS_INSTRUCTION

    return PromptTemplateSet(
        chat_system=CHAT_SYSTEM_TEMPLATE,
        extraction=CHAMP_EXTRACTION_TEMPLATE,
        extraction_sector_instruction=sector_instr,
        reasoning=REASONING_REPORT_TEMPLATE,
        closing=CLOSING_TEMPLATE,
        qualification_judge=QUALIFICATION_JUDGE_TEMPLATE,
    )


def _get_en_templates(sector: str) -> PromptTemplateSet:
    from app.domain.conversation.templates.en.chat_system import CHAT_SYSTEM_TEMPLATE
    from app.domain.conversation.templates.en.extraction import (
        CHAMP_EXTRACTION_TEMPLATE,
        CONSTRUCTION_QUALIFIERS_INSTRUCTION,
        GENERAL_QUALIFIERS_INSTRUCTION,
    )
    from app.domain.conversation.templates.en.reasoning import REASONING_REPORT_TEMPLATE
    from app.domain.conversation.templates.en.closing import CLOSING_TEMPLATE
    from app.domain.conversation.templates.en.qualification_judge import QUALIFICATION_JUDGE_TEMPLATE

    sec = sector.lower().strip()
    sector_instr = GENERAL_QUALIFIERS_INSTRUCTION
    if sec in ("construction",):
        sector_instr = CONSTRUCTION_QUALIFIERS_INSTRUCTION

    return PromptTemplateSet(
        chat_system=CHAT_SYSTEM_TEMPLATE,
        extraction=CHAMP_EXTRACTION_TEMPLATE,
        extraction_sector_instruction=sector_instr,
        reasoning=REASONING_REPORT_TEMPLATE,
        closing=CLOSING_TEMPLATE,
        qualification_judge=QUALIFICATION_JUDGE_TEMPLATE,
    )
