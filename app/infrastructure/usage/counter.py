"""
Usage counter — Redis INCR based hot counter.

Pattern:
    key = `usage:{tenant_id}:{yyyy-mm-dd}:{metric}`
    ops = INCR (atomic)
    TTL = 48h (background job günlük aggregate'i PostgreSQL tenant_usage'a
    flush ettikten sonra otomatik expire)

Metrics (11 adet, plan Section 9'da listeli):
    leads_ingested, leads_qualified, api_calls, llm_tokens,
    champ_extractions, sse_sessions, webhook_deliveries, widget_loads,
    ask_lisent_queries, kb_documents, active_users

Usage:
    counter = RedisUsageCounter(redis)
    await counter.incr(tenant_id, "api_calls")
    count = await counter.get_today(tenant_id, "api_calls")
    month = await counter.get_month(tenant_id, "leads_qualified")
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

# 11 canonical metric names (plan.md Section 9)
KNOWN_METRICS = frozenset(
    {
        "leads_ingested",
        "leads_qualified",
        "api_calls",
        "llm_tokens",
        "champ_extractions",
        "sse_sessions",
        "webhook_deliveries",
        "widget_loads",
        "ask_lisent_queries",
        "kb_documents",
        "active_users",
    }
)

_HOT_TTL_SECONDS = 48 * 3600  # 48 hours


class RedisUsageCounter:
    """Tenant-scoped usage counter backed by Redis INCR."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    @staticmethod
    def _key(tenant_id: UUID, day: date, metric: str) -> str:
        return f"usage:{tenant_id}:{day.isoformat()}:{metric}"

    async def incr(self, tenant_id: UUID, metric: str, amount: int = 1) -> int:
        """Atomic increment. Returns new total for today."""
        if metric not in KNOWN_METRICS:
            log.warning("unknown_usage_metric", metric=metric, tenant_id=str(tenant_id))

        today = date.today()
        key = self._key(tenant_id, today, metric)
        new_value = await self._r.incrby(key, amount)
        # TTL yoksa set et (keep hot window)
        await self._r.expire(key, _HOT_TTL_SECONDS, nx=True)
        return int(new_value)

    async def get_today(self, tenant_id: UUID, metric: str) -> int:
        """Bugünün sayacı."""
        key = self._key(tenant_id, date.today(), metric)
        val = await self._r.get(key)
        return int(val) if val else 0

    async def get_day(self, tenant_id: UUID, day: date, metric: str) -> int:
        key = self._key(tenant_id, day, metric)
        val = await self._r.get(key)
        return int(val) if val else 0

    async def get_month(self, tenant_id: UUID, metric: str) -> dict[str, int]:
        """Bu ayın günlük dağılımı — sadece Redis'te hot olanlar (48h TTL)."""
        today = date.today()
        # Son 31 güne bak
        result: dict[str, int] = {}
        for i in range(31):
            d = today - timedelta(days=i)
            val = await self.get_day(tenant_id, d, metric)
            if val > 0:
                result[d.isoformat()] = val
        return result

    async def get_all_metrics_today(self, tenant_id: UUID) -> dict[str, int]:
        """Bugün için tüm metriklerin değerleri."""
        result: dict[str, int] = {}
        for metric in KNOWN_METRICS:
            result[metric] = await self.get_today(tenant_id, metric)
        return result
