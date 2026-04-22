"""
Verify with Lisent — credential issuance and verification endpoints.

GET /v1/leads/{id}/credential — issue signed JWT for qualified lead
POST /v1/credentials/verify — verify any Lisent-issued credential

Unique differentiator (no competitor has this). Full OpenID4VCI Phase 6'da.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.domain.tenant import credential as cred
from app.infrastructure.db.pool import get_db_pool
from app.interfaces.rest_public.auth import get_current_tenant
from app.ports.tenant import Tenant

log = structlog.get_logger(__name__)

router = APIRouter(tags=["v1-credentials"])


class CredentialIssueResponse(BaseModel):
    credential: str = Field(description="Signed JWT — include in requests to other Lisent-enabled vendors")
    lead_id: UUID
    tenant_id: UUID
    score: int
    threshold: int
    qualified: bool
    framework: str
    expires_at: int


class CredentialVerifyRequest(BaseModel):
    credential: str


class CredentialVerifyResponse(BaseModel):
    valid: bool
    score: int | None = None
    threshold: int | None = None
    qualified: bool | None = None
    framework: str | None = None
    issuer_tenant_id: str | None = None
    issued_at: int | None = None
    expires_at: int | None = None
    reason: str | None = None


# ============================================================================
# Issue
# ============================================================================


@router.get("/leads/{lead_id}/credential", response_model=CredentialIssueResponse)
async def issue_lead_credential(
    lead_id: UUID,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> CredentialIssueResponse:
    """Lead için Verify-with-Lisent credential üret.

    Email credential payload'ında sadece sha256 hash (privacy-preserving).
    Secret: tenant.outbound_webhook_secret varsa onu, yoksa platform api_key_pepper.
    """
    pool = get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant.id}'")
            row = await conn.fetchrow(
                "SELECT id, email, score FROM qualifier_leads WHERE id = $1 AND tenant_id = $2",
                lead_id,
                tenant.id,
            )
    if row is None:
        raise HTTPException(404, "Lead not found")

    threshold = int(tenant.config.get("qualification_threshold", 75))
    settings = get_settings()
    secret = tenant.outbound_webhook_secret or settings.api_key_pepper

    token = cred.issue_credential(
        lead_id=str(lead_id),
        tenant_id=str(tenant.id),
        score=int(row["score"] or 0),
        threshold=threshold,
        framework=tenant.qualification_framework.value,
        email=row["email"],
        secret=secret,
    )

    # Parse back to reflect fields (single source of truth)
    payload = cred.verify_credential(token, secret)

    return CredentialIssueResponse(
        credential=token,
        lead_id=lead_id,
        tenant_id=tenant.id,
        score=payload.score,
        threshold=payload.threshold,
        qualified=payload.qualified,
        framework=payload.framework,
        expires_at=payload.expires_at,
    )


# ============================================================================
# Verify
# ============================================================================


@router.post("/credentials/verify", response_model=CredentialVerifyResponse)
async def verify_credential(
    body: CredentialVerifyRequest,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> CredentialVerifyResponse:
    """Credential'ı doğrula.

    MVP: Tenant kendi secret'ı ile imzaladığı credential'ı verify edebilir
    (self-signed chain). Phase 6 OpenID4VCI ile cross-tenant verification
    public key infrastructure üstünden mümkün olacak.
    """
    pool = get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant.id}'")
            # Find issuer tenant from credential's tenant_id claim (no signature yet)
            try:
                h, p, s = body.credential.split(".")
                import json as _j

                payload_raw = _j.loads(cred._b64url_decode(p))
                issuer_tid = payload_raw.get("tenant_id")
            except Exception:
                return CredentialVerifyResponse(valid=False, reason="malformed credential")

            # Fetch issuer's secret
            if issuer_tid:
                row = await conn.fetchrow(
                    "SELECT outbound_webhook_secret FROM tenants WHERE id = $1",
                    UUID(issuer_tid) if issuer_tid else None,
                )
            else:
                row = None

    settings = get_settings()
    secret = (
        (row["outbound_webhook_secret"] if row and row["outbound_webhook_secret"] else None)
        or settings.api_key_pepper
    )

    try:
        payload = cred.verify_credential(body.credential, secret)
    except cred.CredentialExpired as exc:
        return CredentialVerifyResponse(valid=False, reason=str(exc))
    except cred.CredentialSignatureMismatch:
        return CredentialVerifyResponse(valid=False, reason="signature mismatch")
    except cred.CredentialInvalid as exc:
        return CredentialVerifyResponse(valid=False, reason=str(exc))

    return CredentialVerifyResponse(
        valid=True,
        score=payload.score,
        threshold=payload.threshold,
        qualified=payload.qualified,
        framework=payload.framework,
        issuer_tenant_id=payload.tenant_id,
        issued_at=payload.issued_at,
        expires_at=payload.expires_at,
    )
