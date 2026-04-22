"""
Tenant auth dependency for v1 routes.

Supports 3 auth modes (evaluated in order):
    1. Platform admin token (BFF proxy) — Bearer matches PLATFORM_ADMIN_TOKEN
       env var, tenant resolved from `X-Lisent-Tenant-Id` header. Used by
       crm-web BFF for admin panels (API keys/config/usage).
    2. API key — Bearer sk_live_* / sk_test_* → bcrypt-hashed lookup in
       tenant_api_keys table (Phase 2.A).
    3. Dev slug convenience — raw tenant slug as Bearer, resolve via
       webhook_token → standalone adapter (Phase 1.E dev/test only).

Production: mode 1 + mode 2 used. Mode 3 disabled via
`DISABLE_SLUG_AUTH=true` env (Phase 2+).
"""

from __future__ import annotations

import hmac
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.interfaces.rest_public.di import get_tenant_adapter
from app.ports.tenant import Tenant, TenantNotFoundError, TenantPort, TenantSuspendedError

log = structlog.get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_tenant(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    tenant_adapter: TenantPort = Depends(get_tenant_adapter),
) -> Tenant:
    """Bearer → Tenant (3 mode — docstring yukarıda)."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header (expected Bearer)",
        )

    raw = credentials.credentials
    settings = get_settings()

    # ── Mode 1: Platform admin token (BFF proxy) ────────────────────────────
    if (
        settings.platform_admin_token
        and hmac.compare_digest(raw, settings.platform_admin_token)
    ):
        # Öncelik: X-Lisent-Tenant-Id → direct tenant_id
        # Alternatif: X-Lisent-Source-Ref (+optional X-Lisent-Source-Type)
        #   → legacy CRM company_id lookup (migration 003 backfill üzerinden)
        tenant_header = request.headers.get("x-lisent-tenant-id")
        source_ref_header = request.headers.get("x-lisent-source-ref")
        source_type_header = request.headers.get("x-lisent-source-type", "lisent_crm")

        tenant: Tenant | None = None

        if tenant_header:
            try:
                tenant_uuid = UUID(tenant_header)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="X-Lisent-Tenant-Id must be a valid UUID",
                )
            try:
                tenant = await tenant_adapter.resolve_by_id(tenant_uuid)
            except TenantNotFoundError:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"tenant {tenant_uuid} not found",
                )
            except TenantSuspendedError:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant suspended",
                )
        elif source_ref_header:
            from app.ports.tenant import TenantSourceType

            try:
                source_type = TenantSourceType(source_type_header)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid X-Lisent-Source-Type: {source_type_header!r}",
                )
            tenant = await tenant_adapter.resolve_by_source_ref(
                source_type, source_ref_header
            )
            if tenant is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"tenant for {source_type.value}:{source_ref_header} not found",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Platform admin token requires one of: "
                    "X-Lisent-Tenant-Id or X-Lisent-Source-Ref header"
                ),
            )

        log.debug(
            "auth_platform_admin",
            tenant_id=str(tenant.id),
            via=("tenant_id" if tenant_header else "source_ref"),
        )
        request.state.tenant_id = tenant.id
        request.state.tenant = tenant
        request.state.auth_mode = "platform_admin"
        return tenant

    # ── Mode 2: API key (sk_live_* / sk_test_*) ─────────────────────────────
    try:
        if raw.startswith(("sk_live_", "sk_test_")):
            tenant = await tenant_adapter.resolve_by_api_key(raw)
            request.state.auth_mode = "api_key"
        else:
            # ── Mode 3: Dev slug convenience ────────────────────────────────
            tenant = await tenant_adapter.resolve_by_webhook_token(raw)
            request.state.auth_mode = "slug"
    except TenantNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    except TenantSuspendedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant suspended",
        )
    except NotImplementedError:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Auth method not implemented for this adapter",
        )

    request.state.tenant_id = tenant.id
    request.state.tenant = tenant
    return tenant
