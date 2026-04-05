"""Unit tests for prompt builders (pure string functions)."""
import json
from app.domain.conversation.prompts import (
    build_chat_system_prompt,
    build_champ_extraction_prompt,
    build_reasoning_report_prompt,
    build_handoff_closing_prompt,
)


def test_chat_prompt_contains_lead_data():
    lead = {"contact": {"name": "Ali Veli"}, "project_type": "residential"}
    prompt = build_chat_system_prompt(lead)
    assert "Ali Veli" in prompt
    assert "residential" in prompt


def test_chat_prompt_includes_champ_section_when_provided():
    lead = {"contact": {"name": "Mehmet"}}
    champ = {"challenges_score": 20, "challenges_notes": "Villa inşaatı"}
    prompt = build_chat_system_prompt(lead, champ)
    assert "CHAMP" in prompt
    assert "Villa" in prompt


def test_chat_prompt_includes_gap_instruction():
    lead = {"contact": {"name": "Mehmet"}}
    champ = {"challenges_score": 20, "authority_score": 5, "money_score": 15, "prioritization_score": 10}
    prompt = build_chat_system_prompt(lead, champ)
    assert "authority" in prompt.lower()  # biggest gap should be mentioned


def test_chat_prompt_no_champ_section_when_absent():
    lead = {"contact": {"name": "Mehmet"}}
    prompt = build_chat_system_prompt(lead, None)
    assert "CHAMP Analysis" not in prompt


def test_champ_extraction_prompt_contains_conversation():
    conv = "USER: Bütçem 2 milyon TL\nASSISTANT: Ne zaman başlamayı düşünüyorsunuz?"
    prompt = build_champ_extraction_prompt(conv)
    assert "2 milyon" in prompt
    assert "challenges_score" in prompt
    assert "confidence" in prompt


def test_champ_extraction_prompt_includes_few_shots():
    conv = "USER: Test"
    prompt = build_champ_extraction_prompt(conv)
    assert "Örnek" in prompt  # Turkish few-shot examples should be present


def test_reasoning_report_prompt_contains_score():
    lead = {"contact": {"name": "Test"}}
    breakdown = {"challenges": 25, "total": 85}
    prompt = build_reasoning_report_prompt(lead, 85, breakdown)
    assert "85" in prompt
    assert "summary" in prompt


def test_handoff_closing_prompt_is_nonempty():
    prompt = build_handoff_closing_prompt()
    assert len(prompt) > 10


def test_multilingual_templates_english():
    lead = {"contact": {"name": "John"}}
    config = {"primary_language": "en", "industry_focus": "construction"}
    prompt = build_chat_system_prompt(lead, company_config=config)
    assert "John" in prompt
    assert "CHAMP" in prompt


def test_extraction_prompt_with_current_champ():
    conv = "USER: Test"
    current = {"challenges_score": 15, "authority_score": 10, "money_score": 0, "prioritization_score": 5}
    prompt = build_champ_extraction_prompt(conv, current_champ_json=current)
    assert "Current CHAMP State" in prompt
