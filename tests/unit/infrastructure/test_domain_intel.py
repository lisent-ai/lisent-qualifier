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

    def test_common_freemails_flagged(self):
        for d in ("gmail.com", "Gmail.com", "hotmail.com", "yandex.ru",
                  "yahoo.com.tr", "outlook.com", "protonmail.com"):
            assert is_freemail(d), f"{d} should be freemail"

    def test_corporate_not_freemail(self):
        assert is_freemail("onurinsaat.com.tr") is False


class TestClassifyDomain:
    def test_missing_empty(self):
        assert classify_domain("") == "missing"
        assert classify_domain(None) == "missing"  # type: ignore[arg-type]

    def test_disposable_short_circuit(self):
        assert classify_domain("mailinator.com") == "disposable"
        assert classify_domain("10minutemail.com") == "disposable"

    def test_freemail_bucket(self):
        assert classify_domain("gmail.com") == "freemail"
        assert classify_domain("yandex.ru") == "freemail"

    def test_edu_tr_verified(self):
        assert classify_domain("bogazici.edu.tr") == "corporate_verified"

    def test_gov_tr_verified(self):
        assert classify_domain("tuik.gov.tr") == "corporate_verified"

    def test_com_tr_suspected(self):
        assert classify_domain("onurinsaat.com.tr") == "corporate_suspected"

    def test_generic_com_suspected(self):
        # Corporate-looking but no stronger signal until MX check fires.
        assert classify_domain("example.com") == "corporate_suspected"

    def test_case_insensitive(self):
        assert classify_domain("GMAIL.com") == "freemail"
        assert classify_domain("TUIK.gov.tr") == "corporate_verified"
