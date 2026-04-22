"""
Plan-based rate limiting — Redis sliding window per tenant.

Plan limits (plan.md Section 9):
    free:       60 req/min, 2 concurrent SSE
    starter:    600 req/min, 10 concurrent SSE
    pro:        6000 req/min, 100 concurrent SSE
    enterprise: unlimited
    partner:    10000 req/min, 200 concurrent SSE
    legacy:     unlimited (grandfathered CRM tenants)

Algorithm:
    Sliding log — Redis ZSET with timestamp scores.
    key: rate:{tenant_id}:{minute_bucket}
    on each request: ZADD + ZCARD; if ZCARD > limit → 429
    auto-clean: ZSET expires after 60s
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)


# Plan → req/min limit (None = unlimited)
RATE_LIMITS_PER_MIN: dict[str, int | None] = {
    "free": 60,
    "starter": 600,
    "pro": 6000,
    "enterprise": None,
    "partner": 10000,
    "legacy": None,
}


class RateLimitExceeded(Exception):
    """Rate limit aşıldı — HTTP 429 döndür."""

    def __init__(self, limit: int, window_seconds: int = 60, retry_after: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after = retry_after
        super().__init__(f"Rate limit {limit}/{window_seconds}s exceeded")


class RedisRateLimiter:
    """Sliding log rate limiter backed by Redis ZSET."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    @staticmethod
    def _key(tenant_id: UUID) -> str:
        return f"rate:{tenant_id}"

    async def check_and_consume(
        self, tenant_id: UUID, plan: str, window_seconds: int = 60
    ) -> None:
        """Raises RateLimitExceeded if over limit, else records this request."""
        limit = RATE_LIMITS_PER_MIN.get(plan)
        if limit is None:
            return  # unlimited plans

        now = time.time()
        cutoff = now - window_seconds
        key = self._key(tenant_id)

        # Atomic-ish: cleanup old + count + add + ttl
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zcard(key)
        pipe.zadd(key, {f"{now}:{id(self)}": now})
        pipe.expire(key, window_seconds + 5)
        results = await pipe.execute()

        current_count = int(results[1]) + 1  # +1 for just-added
        if current_count > limit:
            log.warning(
                "rate_limit_exceeded",
                tenant_id=str(tenant_id),
                plan=plan,
                count=current_count,
                limit=limit,
            )
            raise RateLimitExceeded(limit=limit, window_seconds=window_seconds)
