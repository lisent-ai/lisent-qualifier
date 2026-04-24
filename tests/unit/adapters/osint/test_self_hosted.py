"""Phase 3 — SelfHostedOSINTAdapter unit tests (respx httpx mock)."""

from __future__ import annotations

import httpx
import pytest

try:
    import respx

    _HAS_RESPX = True
except ImportError:
    _HAS_RESPX = False

from app.adapters.osint.self_hosted import (
    SelfHostedOSINTAdapter,
    _classify_email_domain,
    _digits_only,
)

pytestmark = pytest.mark.skipif(not _HAS_RESPX, reason="respx missing")


PHONEINFOGA_URL = "http://phoneinfoga-test:5000"
HOLEHE_URL = "http://holehe-test:8000"


class TestHelperFunctions:
    def test_digits_only_strips_non_digits(self):
        assert _digits_only("+90 (555) 123-4567") == "905551234567"
        assert _digits_only(" 555-123 ") == "555123"
        assert _digits_only("") == ""
        assert _digits_only(None) == ""

    def test_classify_email_domain(self):
        # Objective categorization — no "freemail" stigma, no
        # "corporate_suspected" guessing. Matches the neutral vocabulary
        # exposed by domain_intel.classify_domain.
        assert _classify_email_domain("gmail.com") == "public_provider"
        assert _classify_email_domain("mailinator.com") == "disposable"
        assert _classify_email_domain("onurinsaat.com.tr") == "country_tld"
        assert _classify_email_domain("GMAIL.COM") == "public_provider"
        assert _classify_email_domain("") == "missing"


@pytest.fixture
async def adapter():
    a = SelfHostedOSINTAdapter(
        phoneinfoga_url=PHONEINFOGA_URL,
        holehe_url=HOLEHE_URL,
        request_timeout_s=5.0,
    )
    yield a
    await a.aclose()


class TestSelfHostedOSINTAdapter:
    async def test_name_is_self_hosted(self, adapter):
        assert adapter.name == "self_hosted"

    async def test_happy_path_turkish_lead(self, adapter):
        with respx.mock(assert_all_called=False) as mock:
            mock.post(f"{PHONEINFOGA_URL}/api/v2/scanners/local/run").respond(
                200, json={
                    "result": {
                        "raw_local": "5551234567",
                        "local": "0555 123 45 67",
                        "e164": "+905551234567",
                        "international": "905551234567",
                        "country_code": 90,
                        "country": "TR",
                    },
                },
            )
            mock.post(f"{HOLEHE_URL}/check").respond(
                200, json={
                    "email": "ayse@onurinsaat.com.tr",
                    "module_count": 121,
                    "registered_count": 2,
                    "rate_limited_count": 60,
                    "error_count": 12,
                    "registered_sites": ["linkedin", "github"],
                    "any_rate_limited": True,
                },
            )

            profile = await adapter.enrich(
                email="ayse@onurinsaat.com.tr",
                phone="+905551234567",
                name="Ayşe Yılmaz",
            )

        assert profile.provider == "self_hosted"
        assert profile.phone.country == "TR"
        assert profile.phone.country_code == 90
        assert profile.phone.e164 == "+905551234567"
        assert profile.phone.valid is True
        assert profile.email.domain == "onurinsaat.com.tr"
        assert profile.email.domain_type == "country_tld"
        assert profile.email.registered_sites == ["linkedin", "github"]
        assert profile.email.site_count == 2
        assert profile.email.modules_checked == 121
        assert profile.cache_hit is False
        assert profile.elapsed_ms >= 0
        assert any("TR" in n for n in profile.notes)
        assert any("country_tld" in n for n in profile.notes)

    async def test_us_number_also_works(self, adapter):
        with respx.mock(assert_all_called=False) as mock:
            mock.post(f"{PHONEINFOGA_URL}/api/v2/scanners/local/run").respond(
                200, json={
                    "result": {
                        "e164": "+14155552671",
                        "country_code": 1,
                        "country": "US",
                        "local": "(415) 555-2671",
                    },
                },
            )
            # No email provided → holehe not called
            profile = await adapter.enrich(email=None, phone="+14155552671")

        assert profile.phone.country == "US"
        assert profile.phone.country_code == 1
        assert profile.email.domain is None
        assert "email: not provided" in " ".join(profile.notes)

    async def test_phoneinfoga_5xx_falls_back_to_empty_signals(self, adapter):
        with respx.mock(assert_all_called=False) as mock:
            mock.post(f"{PHONEINFOGA_URL}/api/v2/scanners/local/run").respond(500)
            mock.post(f"{HOLEHE_URL}/check").respond(
                200, json={
                    "module_count": 121, "registered_count": 0,
                    "rate_limited_count": 50, "error_count": 10,
                    "registered_sites": [], "any_rate_limited": True,
                },
            )
            profile = await adapter.enrich(
                email="test@gmail.com", phone="+905551234567",
            )

        # Phone fetch failed but email succeeded
        assert profile.phone.country is None
        assert any("phone fetch failed" in n for n in profile.notes)
        assert profile.email.domain == "gmail.com"
        assert profile.email.domain_type == "public_provider"

    async def test_holehe_timeout_preserves_phone_signal(self, adapter):
        with respx.mock(assert_all_called=False) as mock:
            mock.post(f"{PHONEINFOGA_URL}/api/v2/scanners/local/run").respond(
                200,
                json={
                    "result": {
                        "country": "TR",
                        "country_code": 90,
                        "e164": "+905551234567",
                    },
                },
            )
            mock.post(f"{HOLEHE_URL}/check").mock(
                side_effect=httpx.TimeoutException("upstream slow"),
            )
            profile = await adapter.enrich(
                email="test@gmail.com", phone="+905551234567",
            )

        assert profile.phone.country == "TR"
        # Email classification still works (public_provider detected on format alone)
        assert profile.email.domain == "gmail.com"
        assert profile.email.domain_type == "public_provider"
        assert any("fetch failed" in n.lower() or "timeout" in n.lower() for n in profile.notes)

    async def test_both_inputs_none(self, adapter):
        profile = await adapter.enrich(email=None, phone=None)
        assert profile.phone.e164 is None
        assert profile.email.domain is None
        assert "phone: not provided" in profile.notes
        assert "email: not provided" in profile.notes

    async def test_invalid_email_format_skipped(self, adapter):
        profile = await adapter.enrich(email="not-an-email", phone=None)
        assert profile.email.domain is None
        assert any("invalid format" in n for n in profile.notes)

    async def test_short_phone_marked_invalid(self, adapter):
        profile = await adapter.enrich(email=None, phone="+12")
        assert profile.phone.valid is False
        assert any("too short" in n for n in profile.notes)

    async def test_digit_extraction_strips_formatting(self, adapter):
        """Plus prefix, parentheses, dashes → all stripped before POSTing."""
        captured_body = {}

        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(f"{PHONEINFOGA_URL}/api/v2/scanners/local/run").respond(
                200, json={"result": {"country": "TR", "country_code": 90}},
            )
            await adapter.enrich(email=None, phone="+90 (555) 123-4567")
            assert route.called
            req_body = route.calls.last.request.content.decode()
            captured_body["body"] = req_body

        assert '"number": "905551234567"' in captured_body["body"] \
            or '"number":"905551234567"' in captured_body["body"]
