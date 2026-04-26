"""
Template registry — returns prompt templates for a given language (chat) or
sector (pre-score).

Usage:
    # Chat path — multilingual
    templates = TemplateRegistry.get_templates("tr")
    prompt = templates.chat_system

    # Pre-score — always English
    pre_score = TemplateRegistry.get_pre_score_templates(sector="construction")
    system_prompt = pre_score.system_template.format(...)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTemplateSet:
    chat_system: str
    extraction: str
    extraction_sector_instruction: str
    reasoning: str
    closing: str                # backward-compat: visit variant
    qualification_judge: str = ""
    # CTA-variant closings (new).
    closing_visit: str = ""
    closing_calendly: str = ""
    closing_nurture: str = ""


@dataclass(frozen=True)
class PreScoreTemplateSet:
    """English-only templates for the pre-score judge.

    The qualifier always emits English; sales-side translation (per CRM
    sales-rep locale) lives in the CRM frontend.
    """

    persona_labels: dict[str, str]
    persona_temperatures: dict[str, float]
    system_template: str
    user_template: str
    few_shots_block: str
    output_schema: str


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

    @staticmethod
    def get_pre_score_templates(
        sector: str = "construction",
    ) -> PreScoreTemplateSet:
        """Returns the English pre-score template set."""
        return _get_en_pre_score_templates(sector)


_REAL_ESTATE_SECTORS = ("real_estate", "real-estate", "realestate", "emlak", "gayrimenkul")
_CONSTRUCTION_SECTORS = ("construction", "insaat", "inşaat")


def _get_tr_templates(sector: str) -> PromptTemplateSet:
    from app.domain.conversation.templates.tr.chat_system import CHAT_SYSTEM_TEMPLATE
    from app.domain.conversation.templates.tr.extraction import (
        CHAMP_EXTRACTION_TEMPLATE,
        CONSTRUCTION_QUALIFIERS_INSTRUCTION,
        REAL_ESTATE_QUALIFIERS_INSTRUCTION,
        GENERAL_QUALIFIERS_INSTRUCTION,
    )
    from app.domain.conversation.templates.tr.reasoning import REASONING_REPORT_TEMPLATE
    from app.domain.conversation.templates.tr.closing import (
        CLOSING_TEMPLATE_VISIT,
        CLOSING_TEMPLATE_CALENDLY,
        CLOSING_TEMPLATE_NURTURE,
    )
    from app.domain.conversation.templates.tr.qualification_judge import QUALIFICATION_JUDGE_TEMPLATE

    sec = sector.lower().strip()
    sector_instr = GENERAL_QUALIFIERS_INSTRUCTION
    if sec in _CONSTRUCTION_SECTORS:
        sector_instr = CONSTRUCTION_QUALIFIERS_INSTRUCTION
    elif sec in _REAL_ESTATE_SECTORS:
        sector_instr = REAL_ESTATE_QUALIFIERS_INSTRUCTION

    return PromptTemplateSet(
        chat_system=CHAT_SYSTEM_TEMPLATE,
        extraction=CHAMP_EXTRACTION_TEMPLATE,
        extraction_sector_instruction=sector_instr,
        reasoning=REASONING_REPORT_TEMPLATE,
        closing=CLOSING_TEMPLATE_VISIT,
        closing_visit=CLOSING_TEMPLATE_VISIT,
        closing_calendly=CLOSING_TEMPLATE_CALENDLY,
        closing_nurture=CLOSING_TEMPLATE_NURTURE,
        qualification_judge=QUALIFICATION_JUDGE_TEMPLATE,
    )


def _get_en_pre_score_templates(sector: str) -> PreScoreTemplateSet:
    from app.domain.conversation.templates.en.pre_score_judge import (
        FEW_SHOTS_EN_CONSTRUCTION,
        OUTPUT_SCHEMA_EXAMPLE,
        PERSONA_LABELS_EN,
        PERSONA_TEMPERATURES_EN,
        PRE_SCORE_JUDGE_SYSTEM_TEMPLATE,
        PRE_SCORE_JUDGE_USER_TEMPLATE,
    )

    # Sector-specific few-shots — only construction has calibrated examples
    # right now; other sectors fall back to the construction calibration set.
    del sector  # noqa: F841 — placeholder for future sector splits
    return PreScoreTemplateSet(
        persona_labels=PERSONA_LABELS_EN,
        persona_temperatures=PERSONA_TEMPERATURES_EN,
        system_template=PRE_SCORE_JUDGE_SYSTEM_TEMPLATE,
        user_template=PRE_SCORE_JUDGE_USER_TEMPLATE,
        few_shots_block=FEW_SHOTS_EN_CONSTRUCTION,
        output_schema=OUTPUT_SCHEMA_EXAMPLE,
    )


def _get_en_templates(sector: str) -> PromptTemplateSet:
    from app.domain.conversation.templates.en.chat_system import CHAT_SYSTEM_TEMPLATE
    from app.domain.conversation.templates.en.extraction import (
        CHAMP_EXTRACTION_TEMPLATE,
        CONSTRUCTION_QUALIFIERS_INSTRUCTION,
        REAL_ESTATE_QUALIFIERS_INSTRUCTION,
        GENERAL_QUALIFIERS_INSTRUCTION,
    )
    from app.domain.conversation.templates.en.reasoning import REASONING_REPORT_TEMPLATE
    from app.domain.conversation.templates.en.closing import (
        CLOSING_TEMPLATE_VISIT,
        CLOSING_TEMPLATE_CALENDLY,
        CLOSING_TEMPLATE_NURTURE,
    )
    from app.domain.conversation.templates.en.qualification_judge import QUALIFICATION_JUDGE_TEMPLATE

    sec = sector.lower().strip()
    sector_instr = GENERAL_QUALIFIERS_INSTRUCTION
    if sec in _CONSTRUCTION_SECTORS:
        sector_instr = CONSTRUCTION_QUALIFIERS_INSTRUCTION
    elif sec in _REAL_ESTATE_SECTORS:
        sector_instr = REAL_ESTATE_QUALIFIERS_INSTRUCTION

    return PromptTemplateSet(
        chat_system=CHAT_SYSTEM_TEMPLATE,
        extraction=CHAMP_EXTRACTION_TEMPLATE,
        extraction_sector_instruction=sector_instr,
        reasoning=REASONING_REPORT_TEMPLATE,
        closing=CLOSING_TEMPLATE_VISIT,
        closing_visit=CLOSING_TEMPLATE_VISIT,
        closing_calendly=CLOSING_TEMPLATE_CALENDLY,
        closing_nurture=CLOSING_TEMPLATE_NURTURE,
        qualification_judge=QUALIFICATION_JUDGE_TEMPLATE,
    )
