"""Unit tests for WebhookLeadPayload coercion."""
import pytest
from pydantic import ValidationError
from app.api.webhook.schemas import WebhookLeadPayload


PHONE = "05001"  # 5 chars, valid minimum


class TestBudgetCoercion:
    def test_string_budget_coerced_to_int(self):
        p = WebhookLeadPayload(name="Ali", phone=PHONE, budget_amount="2000000")
        assert p.budget_amount == 2_000_000

    def test_formatted_budget_stripped(self):
        p = WebhookLeadPayload(name="Ali", phone=PHONE, budget_amount="1.500.000 TL")
        assert p.budget_amount == 1_500_000

    def test_none_budget_stays_none(self):
        p = WebhookLeadPayload(name="Ali", phone=PHONE, budget_amount=None)
        assert p.budget_amount is None

    def test_empty_string_budget_returns_none(self):
        p = WebhookLeadPayload(name="Ali", phone=PHONE, budget_amount="")
        assert p.budget_amount is None

    def test_int_budget_passthrough(self):
        p = WebhookLeadPayload(name="Ali", phone=PHONE, budget_amount=5_000_000)
        assert p.budget_amount == 5_000_000


class TestRequiredFields:
    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            WebhookLeadPayload(phone=PHONE)

    def test_missing_phone_raises(self):
        with pytest.raises(ValidationError):
            WebhookLeadPayload(name="Ali")

    def test_lead_id_auto_generated(self):
        p = WebhookLeadPayload(name="Ali", phone=PHONE)
        assert p.lead_id  # non-empty UUID


class TestDefaults:
    def test_source_defaults_to_other(self):
        p = WebhookLeadPayload(name="Ali", phone=PHONE)
        assert p.source == "other"

    def test_extra_fields_allowed(self):
        p = WebhookLeadPayload(name="Ali", phone=PHONE, custom_field="extra_data")
        assert p.model_extra.get("custom_field") == "extra_data"
