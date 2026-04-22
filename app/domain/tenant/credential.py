"""
Verify with Lisent — portable lead qualification credentials.

MVP (Phase 2.E): HMAC-SHA256 signed JWT. Stdlib-only (no PyJWT dep).
Phase 6: OpenID4VCI / W3C Verifiable Credentials 2.0 (Feb 2026 GA).

Format (RFC 7519 JWT):
    Header:  {"alg": "HS256", "typ": "JWT", "kid": "lisent-2026"}
    Payload: {
        "iss": "https://api.lisent.ai",
        "sub": sha256(lead.email)[:16],   # PII hiding
        "iat": unix_ts,
        "exp": unix_ts + 90 days,
        "tenant_id": "<uuid>",
        "lead_id": "<uuid>",
        "score": 78,
        "threshold": 70,
        "framework": "champ",
        "qualified": true,
        "issued_by": "lisent-v2",
    }
    Signature: HMAC-SHA256(HEADER + "." + PAYLOAD, secret)

Use cases:
    1. Tenant A qualifies Ali → credential issued
    2. Ali reaches Tenant B — paste credential
    3. Tenant B calls POST /v1/credentials/verify(credential) → validity + score
    4. Tenant B skips re-qualification, routes Ali to high-priority rep

Privacy: sub claim is email hash — raw PII not in credential.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any


_ISS = "https://api.lisent.ai"
_KID = "lisent-2026"
_DEFAULT_EXPIRY_SECONDS = 90 * 24 * 3600  # 90 days


@dataclass(frozen=True)
class CredentialPayload:
    lead_id: str
    tenant_id: str
    score: int
    threshold: int
    framework: str
    email_hash: str  # sha256(email)[:16] — privacy-preserving subject
    issued_at: int
    expires_at: int

    @property
    def qualified(self) -> bool:
        return self.score >= self.threshold


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_credential(
    *,
    lead_id: str,
    tenant_id: str,
    score: int,
    threshold: int,
    framework: str,
    email: str | None,
    secret: str,
    expires_in: int = _DEFAULT_EXPIRY_SECONDS,
) -> str:
    """Signed JWT credential üret. Raw key secret = tenant.outbound_webhook_secret
    veya Lisent platform-wide signing key (şu an `api_key_pepper` reuse)."""
    now = int(time.time())
    email_hash = ""
    if email:
        email_hash = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]

    header = {"alg": "HS256", "typ": "JWT", "kid": _KID}
    payload = {
        "iss": _ISS,
        "sub": email_hash,
        "iat": now,
        "exp": now + expires_in,
        "tenant_id": str(tenant_id),
        "lead_id": str(lead_id),
        "score": int(score),
        "threshold": int(threshold),
        "framework": framework,
        "qualified": score >= threshold,
        "issued_by": "lisent-v2",
    }

    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    s = _b64url_encode(sig)
    return f"{h}.{p}.{s}"


class CredentialInvalid(Exception):
    pass


class CredentialExpired(CredentialInvalid):
    pass


class CredentialSignatureMismatch(CredentialInvalid):
    pass


def verify_credential(token: str, secret: str) -> CredentialPayload:
    """
    JWT'yi decode + signature verify + expiry check.

    Raises:
        CredentialInvalid: format broken
        CredentialSignatureMismatch: signature invalid (tampered or wrong secret)
        CredentialExpired: exp geçmiş
    """
    try:
        h, p, s = token.split(".")
    except ValueError:
        raise CredentialInvalid("Malformed token (expected 3 segments)")

    # Signature verify
    signing_input = f"{h}.{p}".encode()
    expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    try:
        actual_sig = _b64url_decode(s)
    except Exception:
        raise CredentialInvalid("Invalid signature encoding")
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise CredentialSignatureMismatch("Signature mismatch")

    try:
        payload: dict[str, Any] = json.loads(_b64url_decode(p))
    except Exception:
        raise CredentialInvalid("Invalid payload JSON")

    # Expiry
    now = int(time.time())
    exp = int(payload.get("exp", 0))
    if exp and now > exp:
        raise CredentialExpired(f"Credential expired at {exp}")

    return CredentialPayload(
        lead_id=str(payload.get("lead_id", "")),
        tenant_id=str(payload.get("tenant_id", "")),
        score=int(payload.get("score", 0)),
        threshold=int(payload.get("threshold", 0)),
        framework=str(payload.get("framework", "champ")),
        email_hash=str(payload.get("sub", "")),
        issued_at=int(payload.get("iat", 0)),
        expires_at=exp,
    )
