"""Domain intelligence — classifier + crt.sh RDN parse unit tests.

Network-bound layers (MX, WHOIS, crt.sh HTTP, Groq) are not exercised
here — they would require either mocking HTTP round-trips or real
network access. Those are covered by the end-to-end smoke script.
"""
from __future__ import annotations

from app.infrastructure.osint.disposable_list import (
    disposable_list_size,
    is_disposable,
    is_freemail,
)
from app.infrastructure.osint.domain_intel import classify_domain


class TestDisposableList:
    def test_blocklist_loaded(self):
        # Bundled snapshot has 5k+ domains
        assert disposable_list_size() > 3000

    def test_known_disposable_flagged(self):
        assert is_disposable("mailinator.com") is True
        assert is_disposable("MAILINATOR.COM") is True  # case-insensitive

    def test_corporate_not_flagged(self):
        assert is_disposable("onurinsaat.com.tr") is False
        assert is_disposable("google.com") is False

    def test_common_public_providers_flagged(self):
        for d in ("gmail.com", "Gmail.com", "hotmail.com", "yandex.ru",
                  "yahoo.com.tr", "outlook.com", "protonmail.com"):
            assert is_freemail(d), f"{d} should be a public provider"

    def test_corporate_not_public_provider(self):
        assert is_freemail("onurinsaat.com.tr") is False


class TestClassifyDomain:
    def test_missing_empty(self):
        assert classify_domain("") == "missing"
        assert classify_domain(None) == "missing"  # type: ignore[arg-type]

    def test_disposable_short_circuit(self):
        assert classify_domain("mailinator.com") == "disposable"
        assert classify_domain("10minutemail.com") == "disposable"

    def test_public_provider_bucket(self):
        # Gmail/outlook/yandex are NOT stigmatized as "freemail"; they
        # are objectively classified as public_provider and treated as
        # a neutral category by the scorer.
        assert classify_domain("gmail.com") == "public_provider"
        assert classify_domain("yandex.ru") == "public_provider"
        assert classify_domain("outlook.com") == "public_provider"

    def test_registry_tld_tr(self):
        assert classify_domain("bogazici.edu.tr") == "registry_tld"
        assert classify_domain("tuik.gov.tr") == "registry_tld"

    def test_country_tld_tr(self):
        assert classify_domain("onurinsaat.com.tr") == "country_tld"

    def test_country_tld_cctld(self):
        assert classify_domain("kohler.de") == "country_tld"
        assert classify_domain("siemens.fr") == "country_tld"

    def test_generic_tld(self):
        # Custom domain with a generic TLD, not a public provider.
        assert classify_domain("example.com") == "generic_tld"
        assert classify_domain("getbranded.net") == "generic_tld"

    def test_case_insensitive(self):
        assert classify_domain("GMAIL.com") == "public_provider"
        assert classify_domain("TUIK.gov.tr") == "registry_tld"
