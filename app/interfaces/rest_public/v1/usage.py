"""
GET /v1/usage — bu ayın kullanım metrikleri + plan limits.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.infrastructure.redis.client import get_redis
from app.infrastructure.usage.counter import KNOWN_METRICS, RedisUsageCounter
from app.infrastructure.usage.rate_limit import RATE_LIMITS_PER_MIN
from app.interfaces.rest_public.auth import get_current_tenant
from app.ports.tenant import Tenant

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/usage", tags=["v1-usage"])


class UsageResponse(BaseModel):
    tenant_id: str
    plan: str
    today: dict[str, int]
    month_to_date: dict[str, int]
    rate_limit_per_min: int | None


@router.get("", response_model=UsageResponse)
async def get_usage(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> UsageResponse:
    counter = RedisUsageCounter(get_redis())
    today = await counter.get_all_metrics_today(tenant.id)

    mtd: dict[str, int] = {}
    for metric in KNOWN_METRICS:
        days = await counter.get_month(tenant.id, metric)
        mtd[metric] = sum(days.values())

    return UsageResponse(
        tenant_id=str(tenant.id),
        plan=tenant.plan.value,
        today=today,
        month_to_date=mtd,
        rate_limit_per_min=RATE_LIMITS_PER_MIN.get(tenant.plan.value),
    )
