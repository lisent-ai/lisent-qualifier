"""Phase 3 — pre-score queue enqueue tests (fakeredis)."""

from __future__ import annotations

import json
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


class TestEnqueueLead:
    async def test_single_enqueue_atomic(self, redis_mock):
        from app.application.prescore.config import (
            active_tenants_key,
            queue_key,
        )
        from app.application.prescore.queue import enqueue_lead

        tid = uuid4()
        lid = uuid4()
        await enqueue_lead(redis_mock, tenant_id=tid, lead_id=lid)

        assert await redis_mock.llen(queue_key(str(tid))) == 1
        assert await redis_mock.sismember(active_tenants_key(), str(tid))

        raw = await redis_mock.lindex(queue_key(str(tid)), 0)
        job = json.loads(raw)
        assert job["tenant_id"] == str(tid)
        assert job["lead_id"] == str(lid)
        assert job["attempt"] == 0
        assert "enqueued_at_ms" in job

    async def test_multiple_tenants_separate_queues(self, redis_mock):
        from app.application.prescore.config import (
            active_tenants_key,
            queue_key,
        )
        from app.application.prescore.queue import enqueue_lead

        t1, t2 = uuid4(), uuid4()
        await enqueue_lead(redis_mock, tenant_id=t1, lead_id=uuid4())
        await enqueue_lead(redis_mock, tenant_id=t1, lead_id=uuid4())
        await enqueue_lead(redis_mock, tenant_id=t2, lead_id=uuid4())

        assert await redis_mock.llen(queue_key(str(t1))) == 2
        assert await redis_mock.llen(queue_key(str(t2))) == 1
        assert await redis_mock.scard(active_tenants_key()) == 2

    async def test_dequeue_fifo(self, redis_mock):
        from app.application.prescore.queue import (
            dequeue_for_tenant,
            enqueue_lead,
        )

        tid = uuid4()
        ids = [uuid4() for _ in range(3)]
        for lid in ids:
            await enqueue_lead(redis_mock, tenant_id=tid, lead_id=lid)

        popped = []
        while (j := await dequeue_for_tenant(redis_mock, tenant_id=str(tid))) is not None:
            popped.append(j["lead_id"])
        assert popped == [str(lid) for lid in ids]  # FIFO

    async def test_dequeue_empty_returns_none(self, redis_mock):
        from app.application.prescore.queue import dequeue_for_tenant
        assert await dequeue_for_tenant(redis_mock, tenant_id="nonexistent") is None

    async def test_enqueue_with_nonzero_attempt(self, redis_mock):
        from app.application.prescore.queue import (
            dequeue_for_tenant,
            enqueue_lead,
        )
        tid = uuid4()
        await enqueue_lead(redis_mock, tenant_id=tid, lead_id=uuid4(), attempt=2)
        job = await dequeue_for_tenant(redis_mock, tenant_id=str(tid))
        assert job["attempt"] == 2
