"""Tests for the CRM write-through helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.application.crm_sync import (
    try_create_or_upsert_crm_lead,
    try_update_ai_metadata,
)
from app.domain.lead.entities import ContactInfo, Lead
from app.domain.lead.enums import (
    BudgetRange,
    DecisionAuthority,
    LeadSource,
    ProjectType,
    TimelineUrgency,
)


def _sample_lead(lead_id: str = "ext-abc-123") -> Lead:
    return Lead(
        id=lead_id,
        source=LeadSource.INSTAGRAM,
        contact=ContactInfo(name="Ali Veli", phone="05551112233", email="ali@example.com"),
        project_type=ProjectType.RESIDENTIAL,
        budget_range=BudgetRange.BUDGET_1M_3M,
        decision_authority=DecisionAuthority.SOLE,
        timeline_urgency=TimelineUrgency.SHORT,
        notes="Lead note",
    )


class _FakeSettings:
    def __init__(self, *, enabled: bool) -> None:
        self.qualifier_crm_writethrough_enabled = enabled


@pytest.mark.asyncio
async def test_create_noop_when_flag_disabled():
    with patch(
        "app.application.crm_sync.get_settings",
        return_value=_FakeSettings(enabled=False),
    ):
        crm_id = await try_create_or_upsert_crm_lead("co-1", _sample_lead())
    assert crm_id is None


@pytest.mark.asyncio
async def test_create_noop_when_no_company_id():
    with patch(
        "app.application.crm_sync.get_settings",
        return_value=_FakeSettings(enabled=True),
    ):
        crm_id = await try_create_or_upsert_crm_lead("", _sample_lead())
    assert crm_id is None


@pytest.mark.asyncio
async def test_create_calls_rest_client_with_ref_and_returns_id():
    with patch(
        "app.application.crm_sync.get_settings",
        return_value=_FakeSettings(enabled=True),
    ), patch(
        "app.application.crm_sync.create_or_upsert_lead",
        new=AsyncMock(return_value={"id": "crm-lead-42"}),
    ) as mock_create:
        crm_id = await try_create_or_upsert_crm_lead(
            "co-1",
            _sample_lead("ext-abc-123"),
            source_override="ai_qualifier",
        )

    assert crm_id == "crm-lead-42"
    mock_create.assert_awaited_once()
    call = mock_create.call_args
    assert call.kwargs["company_id"] == "co-1"
    assert call.kwargs["qualifier_external_ref"] == "ext-abc-123"
    payload = call.kwargs["lead_data"]
    assert payload["source"] == "ai_qualifier"
    assert payload["name"] == "Ali Veli"
    assert payload["phone"] == "05551112233"
    assert payload["email"] == "ali@example.com"
    assert payload["extra_data"]["project_type"] == "residential"


@pytest.mark.asyncio
async def test_create_returns_none_when_rest_client_fails():
    with patch(
        "app.application.crm_sync.get_settings",
        return_value=_FakeSettings(enabled=True),
    ), patch(
        "app.application.crm_sync.create_or_upsert_lead",
        new=AsyncMock(return_value=None),
    ):
        crm_id = await try_create_or_upsert_crm_lead("co-1", _sample_lead())
    assert crm_id is None


@pytest.mark.asyncio
async def test_update_noop_when_flag_disabled():
    with patch(
        "app.application.crm_sync.get_settings",
        return_value=_FakeSettings(enabled=False),
    ), patch(
        "app.application.crm_sync.update_lead_ai_metadata",
        new=AsyncMock(return_value={"lead_id": "x"}),
    ) as mock_patch:
        ok = await try_update_ai_metadata("crm-lead-42", score=75, status="chatting")
    assert ok is False
    mock_patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_noop_when_no_crm_lead_id():
    with patch(
        "app.application.crm_sync.get_settings",
        return_value=_FakeSettings(enabled=True),
    ), patch(
        "app.application.crm_sync.update_lead_ai_metadata",
        new=AsyncMock(return_value={"lead_id": "x"}),
    ) as mock_patch:
        ok = await try_update_ai_metadata("", score=75)
    assert ok is False
    mock_patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_forwards_fields_and_idempotency_key():
    with patch(
        "app.application.crm_sync.get_settings",
        return_value=_FakeSettings(enabled=True),
    ), patch(
        "app.application.crm_sync.update_lead_ai_metadata",
        new=AsyncMock(return_value={"lead_id": "crm-lead-42"}),
    ) as mock_patch:
        ok = await try_update_ai_metadata(
            "crm-lead-42",
            score=80,
            status="qualified",
            champ={"challenges": 20},
            reasoning={"summary": "hi"},
            score_breakdown={"total": 80},
            path="chat",
            session_id="sess-1",
            idempotency_key="handoff-sess-1",
        )
    assert ok is True
    mock_patch.assert_awaited_once()
    call = mock_patch.call_args
    assert call.kwargs["lead_id"] == "crm-lead-42"
    assert call.kwargs["idempotency_key"] == "handoff-sess-1"
    body = call.kwargs["ai_payload"]
    assert body["ai_score"] == 80
    assert body["ai_status"] == "qualified"
    assert body["ai_champ"] == {"challenges": 20}
    assert body["ai_reasoning"] == {"summary": "hi"}
    assert body["ai_score_breakdown"] == {"total": 80}
    assert body["ai_path"] == "chat"
    assert body["ai_session_id"] == "sess-1"
    assert "ai_last_scored_at" in body
    assert body["actor"] == "ai_qualifier"


@pytest.mark.asyncio
async def test_update_returns_false_on_rest_error():
    with patch(
        "app.application.crm_sync.get_settings",
        return_value=_FakeSettings(enabled=True),
    ), patch(
        "app.application.crm_sync.update_lead_ai_metadata",
        new=AsyncMock(return_value=None),
    ):
        ok = await try_update_ai_metadata("crm-lead-42", score=10)
    assert ok is False
