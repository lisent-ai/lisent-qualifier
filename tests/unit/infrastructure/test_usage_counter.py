"""Phase 2.G — Usage counter unit tests (fakeredis)."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

try:
    import fakeredis.aioredis

    _HAS_FAKEREDIS = True
except ImportError:
    _HAS_FAKEREDIS = False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _HAS_FAKEREDIS, reason="fakeredis missing"),
]


@pytest.fixture
async def redis_mock():
    r = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield r
    await r.flushall()
    await r.aclose()


class TestRedisUsageCounter:
    async def test_incr_increments(self, redis_mock):
        from app.infrastructure.usage.counter import RedisUsageCounter

        c = RedisUsageCounter(redis_mock)
        tid = uuid4()

        assert await c.incr(tid, "api_calls") == 1
        assert await c.incr(tid, "api_calls") == 2
        assert await c.incr(tid, "api_calls", amount=5) == 7

    async def test_get_today_returns_current(self, redis_mock):
        from app.infrastructure.usage.counter import RedisUsageCounter

        c = RedisUsageCounter(redis_mock)
        tid = uuid4()
        await c.incr(tid, "leads_ingested", amount=3)
        assert await c.get_today(tid, "leads_ingested") == 3
        assert await c.get_today(tid, "api_calls") == 0  # untracked metric

    async def test_separate_tenants_independent(self, redis_mock):
        from app.infrastructure.usage.counter import RedisUsageCounter

        c = RedisUsageCounter(redis_mock)
        a, b = uuid4(), uuid4()
        await c.incr(a, "api_calls", 10)
        await c.incr(b, "api_calls", 7)
        assert await c.get_today(a, "api_calls") == 10
        assert await c.get_today(b, "api_calls") == 7

    async def test_all_metrics_today(self, redis_mock):
        from app.infrastructure.usage.counter import RedisUsageCounter

        c = RedisUsageCounter(redis_mock)
        tid = uuid4()
        await c.incr(tid, "leads_ingested", 5)
        await c.incr(tid, "api_calls", 20)
        result = await c.get_all_metrics_today(tid)
        assert result["leads_ingested"] == 5
        assert result["api_calls"] == 20
        assert result["widget_loads"] == 0


class TestRedisRateLimiter:
    async def test_unlimited_plan_never_blocks(self, redis_mock):
        from app.infrastructure.usage.rate_limit import RedisRateLimiter

        rl = RedisRateLimiter(redis_mock)
        tid = uuid4()
        # 1000 request enterprise plan
        for _ in range(1000):
            await rl.check_and_consume(tid, "enterprise", window_seconds=60)

    async def test_free_plan_blocks_after_60(self, redis_mock):
        from app.infrastructure.usage.rate_limit import (
            RateLimitExceeded,
            RedisRateLimiter,
        )

        rl = RedisRateLimiter(redis_mock)
        tid = uuid4()

        # 60 success
        for _ in range(60):
            await rl.check_and_consume(tid, "free", window_seconds=60)

        # 61st → block
        with pytest.raises(RateLimitExceeded) as exc_info:
            await rl.check_and_consume(tid, "free", window_seconds=60)
        assert exc_info.value.limit == 60
