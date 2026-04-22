"""
API Key generation, hashing, and verification.

Security model:
    - Raw key: `sk_live_<32-char-base64url>` or `sk_test_<32-char-base64url>`
      (192 bits entropy — ~1 atom count of universe to brute force)
    - Stored: SHA-256(pepper || raw_key) as hex
      + last_4 characters for UI display ("sk_live_...aB3f")
    - Pepper: env var `API_KEY_PEPPER` (default 'dev_pepper_rotate_in_prod').
      DB leak durumunda pepper olmadan raw_key recover edilemez.

Neden bcrypt değil: API keys high-entropy (192 bits) — rainbow table / brute
force saldırıları pratik olarak imkansız. Slow hash (bcrypt) gereksiz.
SHA-256 constant-time compare yeterli + stdlib-only (dependency-free).

Stripe pattern: sk_live_* / sk_test_* prefix, hash'li storage, last_4 UI.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Literal

# 24 random bytes → 32 url-safe base64 characters
KEY_BYTES = 24


@dataclass(frozen=True)
class GeneratedAPIKey:
    """Fresh API key — raw bir kez döner (insert response), hash+last_4 DB'ye."""

    raw: str
    prefix: str
    last_4: str
    hash: str


def generate_api_key(
    environment: Literal["live", "test"] = "live",
    pepper: str = "dev_pepper_rotate_in_prod",
) -> GeneratedAPIKey:
    """
    Fresh API key yarat.

    Args:
        environment: 'live' (production) veya 'test' (sandbox)
        pepper: DB leak defense — env var'dan gelmeli

    Returns:
        GeneratedAPIKey — raw sadece bir kez döner; hash+last_4 DB'ye yazılır
    """
    body = secrets.token_urlsafe(KEY_BYTES)
    prefix = f"sk_{environment}_"
    raw = f"{prefix}{body}"
    last_4 = body[-4:]
    return GeneratedAPIKey(
        raw=raw,
        prefix=prefix,
        last_4=last_4,
        hash=hash_api_key(raw, pepper=pepper),
    )


def hash_api_key(raw_key: str, pepper: str = "dev_pepper_rotate_in_prod") -> str:
    """SHA-256 hash with pepper — constant-time comparable hex string."""
    mac = hashlib.sha256()
    mac.update(pepper.encode("utf-8"))
    mac.update(b":")
    mac.update(raw_key.encode("utf-8"))
    return mac.hexdigest()


def verify_api_key(
    raw_key: str,
    stored_hash: str,
    pepper: str = "dev_pepper_rotate_in_prod",
) -> bool:
    """Constant-time compare of computed hash vs stored."""
    computed = hash_api_key(raw_key, pepper=pepper)
    return hmac.compare_digest(computed, stored_hash)


def parse_api_key_prefix(raw_key: str) -> tuple[str, str] | None:
    """
    Raw key'den prefix + body ayır.

    Returns:
        (prefix, body) tuple, veya None eğer invalid format.

    Examples:
        'sk_live_abc123' → ('sk_live_', 'abc123')
        'sk_test_xyz' → ('sk_test_', 'xyz')
        'not_a_key' → None
    """
    for prefix in ("sk_live_", "sk_test_"):
        if raw_key.startswith(prefix):
            return prefix, raw_key[len(prefix) :]
    return None
