"""
Rate limit FastAPI dependency — per-tenant, plan-based.

Usage:
    @router.post("/leads", dependencies=[Depends(rate_limit)])
    async def create_lead(...): ...

Or add globally to v1_router. Middleware (per-request ASGI) yerine dependency
tercih ediyoruz çünkü:
    - Auth'tan sonra çalışmalı (tenant.id gerekli)
    - Public /v1/health auth'suz, rate limit'siz kalsın
    - FastAPI Depends composable
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Response, status

from app.infrastructure.redis.client import get_redis
from app.infrastructure.usage.counter import RedisUsageCounter
from app.infrastructure.usage.rate_limit import RateLimitExceeded, RedisRateLimiter
from app.interfaces.rest_public.auth import get_current_tenant
from app.ports.tenant import Tenant


async def enforce_rate_limit(
    response: Response,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> Tenant:
    """Rate limit aş → 429 + Retry-After. Aksi halde sayaç +1, api_calls incr."""
    limiter = RedisRateLimiter(get_redis())
    counter = RedisUsageCounter(get_redis())

    try:
        await limiter.check_and_consume(tenant.id, tenant.plan.value)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit of {exc.limit}/min exceeded for plan {tenant.plan.value}",
            headers={"Retry-After": str(exc.retry_after)},
        )

    # Tracking — fail silent
    try:
        await counter.incr(tenant.id, "api_calls")
    except Exception:
        pass

    return tenant
