"""
API Key management endpoints — POST / GET / DELETE /v1/api-keys.

Tenant owner/admin kendi tenant'ı için API key yaratır, listeler, revoke eder.
Raw key sadece create response'unda döner (SHA-256 hash'li saklanır).

Auth: bu endpoint'ler **kendileri de** API key ister — bootstrap sorunu çözümü:
    - İlk API key: `app_runtime` superuser veya manual SQL insert (admin setup)
    - Sonraki key'ler: mevcut API key (admin-scope'lu) ile yaratılır
    - Alternatif: OAuth2 admin session (Phase 2.I-2.L)

Phase 2.A: basit API key auth ile CRUD. Scope enforcement Phase 2 sonraki
alt-adımda `require_scope()` dependency ile eklenecek.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.domain.tenant.api_key import generate_api_key
from app.infrastructure.db import tenant_api_key_repo
from app.infrastructure.db.pool import get_db_pool
from app.interfaces.rest_public.auth import get_current_tenant
from app.ports.tenant import Tenant

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api-keys", tags=["v1-api-keys"])


# ============================================================================
# Request / Response schemas
# ============================================================================


class CreateAPIKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255, description="Human label, e.g. 'HubSpot integration'")
    scopes: list[str] = Field(default_factory=lambda: ["lead:read", "lead:write"])
    environment: str = Field(default="live", pattern="^(live|test)$")
    expires_at: datetime | None = Field(default=None, description="Optional expiry (UTC); null = never")


class APIKeyPublic(BaseModel):
    """Public API key record — hash kolonu hariç."""

    id: UUID
    tenant_id: UUID
    prefix: str
    last_4: str
    name: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class CreateAPIKeyResponse(APIKeyPublic):
    """Create response INCLUDES raw key — tek ve son kez döner."""

    raw_key: str = Field(description="⚠️ Save this — raw key shown only once, never retrievable again.")


# ============================================================================
# Endpoints
# ============================================================================


@router.post("", response_model=CreateAPIKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: CreateAPIKeyRequest,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> CreateAPIKeyResponse:
    """Yeni API key yarat. Raw key sadece bu response'ta döner."""
    settings = get_settings()

    generated = generate_api_key(
        environment=body.environment,  # type: ignore[arg-type]
        pepper=settings.api_key_pepper,
    )

    pool = get_db_pool()
    async with pool.acquire() as conn:
        row = await tenant_api_key_repo.insert_api_key(
            conn,
            tenant_id=tenant.id,
            prefix=generated.prefix,
            last_4=generated.last_4,
            hash_hex=generated.hash,
            name=body.name,
            scopes=body.scopes,
            expires_at=body.expires_at,
        )

    log.info(
        "api_key_created",
        tenant_id=str(tenant.id),
        key_id=str(row["id"]),
        prefix=generated.prefix,
        last_4=generated.last_4,
    )

    return CreateAPIKeyResponse(
        id=row["id"],
        tenant_id=row["tenant_id"],
        prefix=row["prefix"],
        last_4=row["last_4"],
        name=row["name"],
        scopes=list(row["scopes"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
        raw_key=generated.raw,
    )


@router.get("", response_model=list[APIKeyPublic])
async def list_api_keys(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    include_revoked: bool = False,
) -> list[APIKeyPublic]:
    """Tenant'ın API key'lerini listele (hash'siz)."""
    pool = get_db_pool()
    async with pool.acquire() as conn:
        rows = await tenant_api_key_repo.list_by_tenant(
            conn, tenant.id, include_revoked=include_revoked
        )
    return [
        APIKeyPublic(
            id=r["id"],
            tenant_id=r["tenant_id"],
            prefix=r["prefix"],
            last_4=r["last_4"],
            name=r["name"],
            scopes=list(r["scopes"]),
            created_at=r["created_at"],
            expires_at=r["expires_at"],
            last_used_at=r["last_used_at"],
            revoked_at=r["revoked_at"],
        )
        for r in rows
    ]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: UUID,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> None:
    """API key'i revoke et (soft delete)."""
    pool = get_db_pool()
    async with pool.acquire() as conn:
        revoked = await tenant_api_key_repo.revoke(conn, key_id, tenant.id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or already revoked",
        )
    log.info("api_key_revoked", tenant_id=str(tenant.id), key_id=str(key_id))
