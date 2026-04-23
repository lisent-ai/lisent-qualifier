"""
HMAC-SHA256 signing for outbound webhooks.

Design mirrors GitHub/Stripe: the signature is computed over
`{timestamp}.{raw_body}` to bind the payload to a timestamp and block
replay attacks (consumer verifies `|now - timestamp| <= window`).

All values are bytes-safe — the consumer library must reconstruct the
exact `{ts}.{body}` string and compute `sha256=<hex>` using its shared
secret. `hmac.compare_digest` is used for verification to avoid timing
attacks.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from app.application.webhook.config import SIGNATURE_TIMESTAMP_WINDOW_SECONDS

SIGNATURE_PREFIX = "sha256="


def sign_payload(secret: str, timestamp_ms: int, body: bytes) -> str:
    """Return `sha256=<hex>` for `{timestamp_ms}.{body}`.

    `secret` is the tenant's `outbound_webhook_secret`. `timestamp_ms` is
    the exact value carried in the `X-Lisent-Timestamp` header.
    """
    if not secret:
        raise ValueError("webhook secret required")
    mac = hmac.new(
        secret.encode("utf-8"),
        msg=f"{timestamp_ms}.".encode("utf-8") + body,
        digestmod=hashlib.sha256,
    )
    return f"{SIGNATURE_PREFIX}{mac.hexdigest()}"


def verify_signature(
    secret: str,
    timestamp_ms: int,
    body: bytes,
    received_signature: str,
    *,
    window_seconds: int = SIGNATURE_TIMESTAMP_WINDOW_SECONDS,
    now_ms: int | None = None,
) -> bool:
    """Consumer-side verification helper.

    Exposed primarily for tests and future first-party consumers (widget,
    internal services). Third-party consumers reimplement in their stack.

    Returns True iff:
        * `|now - timestamp_ms| <= window_seconds * 1000`
        * constant-time HMAC match on `{ts}.{body}`
    """
    if not received_signature.startswith(SIGNATURE_PREFIX):
        return False

    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(current_ms - timestamp_ms) > window_seconds * 1000:
        return False

    expected = sign_payload(secret, timestamp_ms, body)
    return hmac.compare_digest(expected, received_signature)
