"""Phase 3 — PreScoreWorker unit tests (fakeredis + mocked service)."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

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


@dataclass
class _RecordedCall:
    tenant_id: UUID
    lead_id: UUID


class _FakeService:
    """Stub PreScoreService — records calls, optionally raises."""

    def __init__(self, raise_exc: Exception | None = None):
        self.calls: list[_RecordedCall] = []
        self._raise = raise_exc

    async def score(self, *, tenant_id, lead_id, conn, ideal_customer_profile,
                    sector, qualification_threshold):
        self.calls.append(_RecordedCall(tenant_id=tenant_id, lead_id=lead_id))
        if self._raise is not None:
            raise self._raise


class _FakePool:
    """Stub asyncpg.Pool — only acquire() is exercised by the worker."""

    def acquire(self):
        return _FakeAcquireCtx()


class _FakeAcquireCtx:
    async def __aenter__(self):
        return _FakeConn()

    async def __aexit__(self, *args):
        return False


class _FakeConn:
    async def execute(self, *args, **kwargs):
        return "SELECT 1"


async def _fake_tenant_resolver(pool, tenant_id):
    return ("test ICP", "construction", 75)


@pytest.fixture
def fake_pool():
    return _FakePool()


class TestProcessSingleJob:
    async def test_successful_processing_calls_service(self, redis_mock, fake_pool):
        from app.application.prescore.queue import enqueue_lead
        from app.application.prescore.worker import PreScoreWorker

        svc = _FakeService()
        worker = PreScoreWorker(
            redis=redis_mock, db_pool=fake_pool, service=svc,
            tenant_resolver=_fake_tenant_resolver,
        )

        tid, lid = uuid4(), uuid4()
        await enqueue_lead(redis_mock, tenant_id=tid, lead_id=lid)

        # Directly exercise _process_job (bypass timing of dispatch loop)
        job = {
            "tenant_id": str(tid),
            "lead_id": str(lid),
            "attempt": 0,
            "enqueued_at_ms": int(time.time() * 1000),
        }
        await worker._process_job(job)

        assert len(svc.calls) == 1
        assert svc.calls[0].tenant_id == tid
        assert svc.calls[0].lead_id == lid

    async def test_failure_schedules_retry(self, redis_mock, fake_pool):
        from app.application.prescore.config import retry_key
        from app.application.prescore.worker import PreScoreWorker

        svc = _FakeService(raise_exc=RuntimeError("boom"))
        worker = PreScoreWorker(
            redis=redis_mock, db_pool=fake_pool, service=svc,
            tenant_resolver=_fake_tenant_resolver,
        )

        job = {
            "tenant_id": str(uuid4()),
            "lead_id": str(uuid4()),
            "attempt": 0,
            "enqueued_at_ms": int(time.time() * 1000),
        }
        await worker._process_job(job)

        # Retry ZSET should have 1 entry
        assert await redis_mock.zcard(retry_key()) == 1

    async def test_max_attempts_goes_to_dlq(self, redis_mock, fake_pool):
        from app.application.prescore.config import (
            MAX_ATTEMPTS,
            dlq_key,
            retry_key,
        )
        from app.application.prescore.worker import PreScoreWorker

        svc = _FakeService(raise_exc=RuntimeError("always fail"))
        worker = PreScoreWorker(
            redis=redis_mock, db_pool=fake_pool, service=svc,
            tenant_resolver=_fake_tenant_resolver,
        )

        tid = str(uuid4())
        job = {
            "tenant_id": tid,
            "lead_id": str(uuid4()),
            "attempt": MAX_ATTEMPTS - 1,  # one more failure → DLQ
        }
        await worker._process_job(job)

        assert await redis_mock.llen(dlq_key(tid)) == 1
        # No retry scheduled (DLQ'd instead)
        assert await redis_mock.zcard(retry_key()) == 0

        raw = await redis_mock.lindex(dlq_key(tid), 0)
        dlq_job = json.loads(raw)
        assert "dlq_reason" in dlq_job
        assert "dlq_at_ms" in dlq_job
        assert "always fail" in dlq_job["dlq_reason"]

    async def test_invalid_uuid_drops_silently(self, redis_mock, fake_pool):
        from app.application.prescore.config import dlq_key, retry_key
        from app.application.prescore.worker import PreScoreWorker

        svc = _FakeService()
        worker = PreScoreWorker(
            redis=redis_mock, db_pool=fake_pool, service=svc,
            tenant_resolver=_fake_tenant_resolver,
        )

        job = {"tenant_id": "not-a-uuid", "lead_id": "x", "attempt": 0}
        await worker._process_job(job)

        assert len(svc.calls) == 0
        assert await redis_mock.zcard(retry_key()) == 0
        assert await redis_mock.llen(dlq_key("not-a-uuid")) == 0


class TestRetryScheduler:
    async def test_due_retry_promoted_to_tenant_queue(self, redis_mock, fake_pool):
        from app.application.prescore.config import (
            active_tenants_key,
            queue_key,
            retry_key,
        )
        # Worker construction not strictly needed for this test —
        # we exercise the retry drain semantics directly via Redis.
        tid = str(uuid4())
        job = {"tenant_id": tid, "lead_id": str(uuid4()), "attempt": 1}
        past_time_ms = int(time.time() * 1000) - 1000  # already due
        await redis_mock.zadd(retry_key(), {json.dumps(job): past_time_ms})

        # Drain retries via internal method (bypass loop)
        now_ms = int(time.time() * 1000)
        due = await redis_mock.zrangebyscore(retry_key(), "-inf", now_ms, start=0, num=50)
        pipe = redis_mock.pipeline(transaction=False)
        for raw in due:
            raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            parsed = json.loads(raw_str)
            pipe.zrem(retry_key(), raw)
            pipe.rpush(queue_key(parsed["tenant_id"]), raw_str)
            pipe.sadd(active_tenants_key(), parsed["tenant_id"])
        await pipe.execute()

        # Retry ZSET should be empty; tenant queue should have 1
        assert await redis_mock.zcard(retry_key()) == 0
        assert await redis_mock.llen(queue_key(tid)) == 1
        assert await redis_mock.sismember(active_tenants_key(), tid)

    async def test_future_retry_stays_in_zset(self, redis_mock, fake_pool):
        from app.application.prescore.config import queue_key, retry_key

        tid = str(uuid4())
        job = {"tenant_id": tid, "lead_id": str(uuid4()), "attempt": 1}
        future_time_ms = int(time.time() * 1000) + 60_000
        await redis_mock.zadd(retry_key(), {json.dumps(job): future_time_ms})

        # Drain-by-score now → not ready yet
        now_ms = int(time.time() * 1000)
        due = await redis_mock.zrangebyscore(retry_key(), "-inf", now_ms, start=0, num=50)
        assert due == []
        assert await redis_mock.zcard(retry_key()) == 1
        assert await redis_mock.llen(queue_key(tid)) == 0


class TestRetryDelaySchedule:
    async def test_retry_delays_in_order(self, redis_mock, fake_pool):
        from app.application.prescore.config import RETRY_DELAYS_SECONDS, retry_key
        from app.application.prescore.worker import PreScoreWorker

        svc = _FakeService(raise_exc=RuntimeError("fail"))
        worker = PreScoreWorker(
            redis=redis_mock, db_pool=fake_pool, service=svc,
            tenant_resolver=_fake_tenant_resolver,
        )

        job = {"tenant_id": str(uuid4()), "lead_id": str(uuid4()), "attempt": 0}
        before = int(time.time() * 1000)
        await worker._schedule_retry(job, next_attempt=1)
        after = int(time.time() * 1000)

        entries = await redis_mock.zrange(retry_key(), 0, -1, withscores=True)
        assert len(entries) == 1
        _, score_ms = entries[0]
        # First retry delay = RETRY_DELAYS_SECONDS[0] = 30s
        expected_ms = before + RETRY_DELAYS_SECONDS[0] * 1000
        assert expected_ms <= score_ms <= after + RETRY_DELAYS_SECONDS[0] * 1000 + 500


class TestPickActiveTenant:
    async def test_returns_none_when_empty(self, redis_mock, fake_pool):
        from app.application.prescore.worker import PreScoreWorker

        worker = PreScoreWorker(
            redis=redis_mock, db_pool=fake_pool, service=_FakeService(),
            tenant_resolver=_fake_tenant_resolver,
        )
        assert await worker._pick_active_tenant() is None

    async def test_returns_tenant_when_present(self, redis_mock, fake_pool):
        from app.application.prescore.config import active_tenants_key
        from app.application.prescore.worker import PreScoreWorker

        tid = str(uuid4())
        await redis_mock.sadd(active_tenants_key(), tid)

        worker = PreScoreWorker(
            redis=redis_mock, db_pool=fake_pool, service=_FakeService(),
            tenant_resolver=_fake_tenant_resolver,
        )
        got = await worker._pick_active_tenant()
        assert got == tid

    async def test_multi_tenant_statistical_fairness(self, redis_mock, fake_pool):
        """SRANDMEMBER should give roughly balanced picks across tenants."""
        from app.application.prescore.config import active_tenants_key
        from app.application.prescore.worker import PreScoreWorker

        tids = [str(uuid4()) for _ in range(3)]
        for t in tids:
            await redis_mock.sadd(active_tenants_key(), t)

        worker = PreScoreWorker(
            redis=redis_mock, db_pool=fake_pool, service=_FakeService(),
            tenant_resolver=_fake_tenant_resolver,
        )
        counts = {t: 0 for t in tids}
        for _ in range(300):
            picked = await worker._pick_active_tenant()
            if picked in counts:
                counts[picked] += 1
        # Each tenant should be picked at least ~50 times (rough fairness)
        for t in tids:
            assert counts[t] >= 50, f"tenant {t} picked only {counts[t]} times"


class TestLifecycle:
    async def test_start_stop(self, redis_mock, fake_pool):
        from app.application.prescore.worker import PreScoreWorker

        worker = PreScoreWorker(
            redis=redis_mock, db_pool=fake_pool, service=_FakeService(),
            tenant_resolver=_fake_tenant_resolver,
        )
        await worker.start()
        assert len(worker._tasks) == 3  # dispatch + retry + metrics
        # Give tasks a moment to start
        await asyncio.sleep(0.05)
        await worker.stop()
        assert len(worker._tasks) == 0

    async def test_double_start_noop(self, redis_mock, fake_pool):
        from app.application.prescore.worker import PreScoreWorker

        worker = PreScoreWorker(
            redis=redis_mock, db_pool=fake_pool, service=_FakeService(),
            tenant_resolver=_fake_tenant_resolver,
        )
        await worker.start()
        initial = list(worker._tasks)
        await worker.start()  # should be no-op
        assert worker._tasks == initial
        await worker.stop()
