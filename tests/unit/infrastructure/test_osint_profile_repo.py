"""Phase 3 — osint_profile_repo pure-function unit tests.

DB call'larını test etmek için integration test'e gerek var (asyncpg pool
lazım). Unit seviyede: make_key_hash determinism + normalization.
"""

from __future__ import annotations

from app.infrastructure.db.osint_profile_repo import make_key_hash


class TestMakeKeyHash:
    def test_deterministic(self):
        h1 = make_key_hash("ayse@onurinsaat.com.tr", "+905551234567")
        h2 = make_key_hash("ayse@onurinsaat.com.tr", "+905551234567")
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex

    def test_email_case_insensitive(self):
        h_lower = make_key_hash("ayse@onurinsaat.com.tr", "+905551234567")
        h_upper = make_key_hash("AYSE@ONURINSAAT.COM.TR", "+905551234567")
        h_mixed = make_key_hash("Ayse@OnurInsaat.com.tr", "+905551234567")
        assert h_lower == h_upper == h_mixed

    def test_email_whitespace_stripped(self):
        h1 = make_key_hash("ayse@onurinsaat.com.tr", "+905551234567")
        h2 = make_key_hash("  ayse@onurinsaat.com.tr  ", "+905551234567")
        assert h1 == h2

    def test_phone_non_digits_stripped(self):
        h1 = make_key_hash("test@x.com", "+905551234567")
        h2 = make_key_hash("test@x.com", "+90 (555) 123-4567")
        h3 = make_key_hash("test@x.com", "90 555 123 45 67")
        assert h1 == h2 == h3

    def test_different_emails_different_hashes(self):
        assert make_key_hash("a@x.com", "+1") != make_key_hash("b@x.com", "+1")

    def test_different_phones_different_hashes(self):
        assert make_key_hash("a@x.com", "+1") != make_key_hash("a@x.com", "+2")

    def test_both_empty_produces_stable_hash(self):
        """Defensive — realistic case değil ama exception atmamalı."""
        h1 = make_key_hash("", "")
        h2 = make_key_hash(None, None)
        assert h1 == h2
        assert len(h1) == 64

    def test_email_only(self):
        h = make_key_hash("test@x.com", "")
        assert len(h) == 64
        assert h != make_key_hash("", "")

    def test_phone_only(self):
        h = make_key_hash("", "+905551234567")
        assert len(h) == 64
        assert h != make_key_hash("", "")
