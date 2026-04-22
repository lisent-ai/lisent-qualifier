"""Unit tests for app.domain.tenant.api_key crypto helpers."""

from __future__ import annotations

from app.domain.tenant.api_key import (
    generate_api_key,
    hash_api_key,
    parse_api_key_prefix,
    verify_api_key,
)


class TestGenerateAPIKey:
    def test_live_prefix(self):
        k = generate_api_key("live", pepper="x")
        assert k.raw.startswith("sk_live_")
        assert k.prefix == "sk_live_"

    def test_test_prefix(self):
        k = generate_api_key("test", pepper="x")
        assert k.raw.startswith("sk_test_")
        assert k.prefix == "sk_test_"

    def test_high_entropy(self):
        """24 random bytes → 32 base64url chars → 40 total."""
        k = generate_api_key("live", pepper="x")
        assert len(k.raw) >= 40

    def test_unique_keys(self):
        """1000 key'de collision olmamalı."""
        keys = {generate_api_key("live", pepper="x").raw for _ in range(1000)}
        assert len(keys) == 1000

    def test_last_4_matches_raw(self):
        k = generate_api_key("live", pepper="x")
        assert k.raw.endswith(k.last_4)
        assert len(k.last_4) == 4

    def test_hash_is_sha256_hex(self):
        k = generate_api_key("live", pepper="x")
        assert len(k.hash) == 64  # sha256 = 256 bits = 64 hex chars
        assert all(c in "0123456789abcdef" for c in k.hash)


class TestHashAPIKey:
    def test_deterministic(self):
        assert hash_api_key("sk_live_abc", pepper="p") == hash_api_key("sk_live_abc", pepper="p")

    def test_pepper_affects_hash(self):
        a = hash_api_key("sk_live_abc", pepper="p1")
        b = hash_api_key("sk_live_abc", pepper="p2")
        assert a != b

    def test_different_keys_different_hashes(self):
        a = hash_api_key("sk_live_abc", pepper="p")
        b = hash_api_key("sk_live_def", pepper="p")
        assert a != b


class TestVerifyAPIKey:
    def test_verify_success(self):
        k = generate_api_key("live", pepper="secret")
        assert verify_api_key(k.raw, k.hash, pepper="secret") is True

    def test_wrong_pepper_fails(self):
        k = generate_api_key("live", pepper="secret")
        assert verify_api_key(k.raw, k.hash, pepper="wrong") is False

    def test_wrong_key_fails(self):
        k = generate_api_key("live", pepper="secret")
        assert verify_api_key("sk_live_DIFFERENT", k.hash, pepper="secret") is False


class TestParseAPIKeyPrefix:
    def test_parse_live(self):
        result = parse_api_key_prefix("sk_live_abc123xyz")
        assert result == ("sk_live_", "abc123xyz")

    def test_parse_test(self):
        result = parse_api_key_prefix("sk_test_xyz")
        assert result == ("sk_test_", "xyz")

    def test_invalid_returns_none(self):
        assert parse_api_key_prefix("not_a_key") is None
        assert parse_api_key_prefix("") is None
        assert parse_api_key_prefix("sk_unknown_xxx") is None
