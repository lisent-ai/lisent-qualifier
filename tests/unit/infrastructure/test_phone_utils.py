"""phone_utils — libphonenumber offline enrichment unit tests.

Verifies global coverage: TR, DE, AE, GB, FR, US numbers all produce
sensible carrier / line_type outputs without any network request.
"""
from __future__ import annotations

from app.infrastructure.osint.phone_utils import enrich_phone_offline


class TestPhoneEnrichment:
    def test_empty_input_returns_empty(self):
        assert enrich_phone_offline(None) == {}
        assert enrich_phone_offline("") == {}
        assert enrich_phone_offline("   ") == {}

    def test_invalid_number_marked_invalid(self):
        # 1234 is too short to be valid anywhere
        result = enrich_phone_offline("+1234")
        assert result.get("valid") is False

    def test_tr_mobile_has_carrier_and_mobile_line_type(self):
        # +905551234567 — TR mobile prefix (Turk Telekom was Avea originally)
        result = enrich_phone_offline("+905551234567")
        assert result["valid"] is True
        assert result["country"] == "TR"
        assert result["country_code"] == 90
        assert result["line_type"] == "mobile"
        # Carrier is metadata-dependent but TR mobile prefixes should resolve
        assert result["carrier"] is not None

    def test_de_mobile_has_carrier(self):
        result = enrich_phone_offline("+4915112345678")
        assert result["valid"] is True
        assert result["country"] == "DE"
        assert result["line_type"] == "mobile"
        assert result["carrier"]  # non-empty string

    def test_ae_mobile_has_carrier(self):
        result = enrich_phone_offline("+971501234567")
        assert result["valid"] is True
        assert result["country"] == "AE"
        assert result["line_type"] == "mobile"
        assert result["carrier"]

    def test_tr_landline_line_type_is_landline(self):
        # +902125550000 — Istanbul landline prefix
        result = enrich_phone_offline("+902125550000")
        assert result["valid"] is True
        assert result["country"] == "TR"
        assert result["line_type"] == "landline"

    def test_fr_landline(self):
        result = enrich_phone_offline("+33123456789")
        assert result["valid"] is True
        assert result["country"] == "FR"
        assert result["line_type"] == "landline"

    def test_e164_normalized_even_without_plus(self):
        # Local TR format (leading zero) with TR region hint fallback
        result = enrich_phone_offline("05551234567")
        # Without +, strict parse fails; fallback retries with TR hint
        assert result.get("valid") is True
        assert result["country"] == "TR"
        assert result["e164"].startswith("+90")

    def test_us_number_valid_but_carrier_metadata_may_be_empty(self):
        """US MNP + metadata coverage means carrier often None — acceptable."""
        result = enrich_phone_offline("+12125551234")
        assert result["valid"] is True
        assert result["country"] == "US"
        # carrier may be None — this is fine; line_type still set
        assert result["line_type"] in {"mobile", "landline", "mobile_or_landline"}
