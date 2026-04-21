"""
API Key authentication dependency for v1 routes.

Phase 1.E (şimdi): Placeholder — `Authorization: Bearer sk_live_...` header'ını
parse eder, bcrypt hash lookup Phase 2'de eklenecek. Şu anda raw token'ı tenant
slug olarak kabul eder (dev/test için convenience).

Phase 2'de:
    - `tenant_api_keys` tablosunda bcrypt hash compare (constant-time)
    - `last_used_at` update
    - scope validation
    - rate limiting hook
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.interfaces.rest_public.di import get_tenant_adapter
from app.ports.tenant import Tenant, TenantNotFoundError, TenantPort, TenantSuspendedError

log = structlog.get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_tenant(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    tenant_adapter: TenantPort = Depends(get_tenant_adapter),
) -> Tenant:
    """API key veya slug-based dev auth'tan Tenant resolve eder.

    Phase 1.E dev mode:
        - `Authorization: Bearer sk_test_<slug>` → resolve_by_webhook_token(slug)
        - Yoksa 401

    Phase 2:
        - `Authorization: Bearer sk_live_<token>` → bcrypt hash lookup
        - `Authorization: Bearer eyJ...` → JWT decode + claim verify
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header (expected Bearer)",
        )

    raw = credentials.credentials
    try:
        # Dev convenience: sk_test_<slug> → slug lookup
        # Prod: API key bcrypt → Phase 2
        if raw.startswith("sk_test_"):
            slug_or_token = raw[len("sk_test_") :]
        elif raw.startswith("sk_live_"):
            slug_or_token = raw  # production yolu — Phase 2 bcrypt lookup
        else:
            slug_or_token = raw  # fallback

        tenant = await tenant_adapter.resolve_by_webhook_token(slug_or_token)
    except TenantNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    except TenantSuspendedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant suspended",
        )
    except NotImplementedError:
        # Dev fallback: bir test adapter'ı bu method'u yapmamış olabilir
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Auth method not implemented for this adapter",
        )

    # Request state'e tenant_id koy — middleware bunu kullanabilir
    request.state.tenant_id = tenant.id
    request.state.tenant = tenant
    return tenant
