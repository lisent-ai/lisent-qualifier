"""Unit tests for prompt builders (pure string functions)."""
import json
from app.domain.conversation.prompts import (
    build_chat_system_prompt,
    build_bant_extraction_prompt,
    build_reasoning_report_prompt,
    build_handoff_closing_prompt,
)


def test_chat_prompt_contains_lead_data():
    lead = {"contact": {"name": "Ali Veli"}, "project_type": "residential"}
    prompt = build_chat_system_prompt(lead)
    assert "Ali Veli" in prompt
    assert "residential" in prompt


def test_chat_prompt_includes_bant_section_when_provided():
    lead = {"contact": {"name": "Mehmet"}}
    bant = {"budget_score": 20, "need_notes": "Villa inşaatı"}
    prompt = build_chat_system_prompt(lead, bant)
    assert "BANT" in prompt
    assert "Villa" in prompt


def test_chat_prompt_no_bant_section_when_absent():
    lead = {"contact": {"name": "Mehmet"}}
    prompt = build_chat_system_prompt(lead, None)
    assert "BANT Bilgileri" not in prompt


def test_bant_extraction_prompt_contains_conversation():
    conv = "USER: Bütçem 2 milyon TL\nASSISTANT: Ne zaman başlamayı düşünüyorsunuz?"
    prompt = build_bant_extraction_prompt(conv)
    assert "2 milyon" in prompt
    assert "budget_score" in prompt
    assert "confidence" in prompt


def test_reasoning_report_prompt_contains_score():
    lead = {"contact": {"name": "Test"}}
    breakdown = {"budget": 25, "total": 85}
    prompt = build_reasoning_report_prompt(lead, 85, breakdown)
    assert "85" in prompt
    assert "summary" in prompt


def test_handoff_closing_prompt_is_nonempty():
    prompt = build_handoff_closing_prompt()
    assert len(prompt) > 10
