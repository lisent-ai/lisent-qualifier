"""Pre-score queue — enqueue API.

Intake handler (webhook + /v1/leads POST) lead'i DB'ye yazdıktan hemen sonra
bu modülün `enqueue_lead()` fonksiyonunu çağırır. Atomic Redis MULTI:
    RPUSH prescore:queue:{tenant_id} <job>
    SADD  prescore:active_tenants   tenant_id

Worker fair scheduler `prescore:active_tenants` SET'i kullanarak tenant
seçer, `LPOP prescore:queue:{tid}` ile iş alır. Queue boşalırsa SET'ten
silinir (worker en son adım).

Job format (JSON):
    {
        "tenant_id":       "<uuid>",
        "lead_id":         "<uuid>",  # qualifier_leads.id (DB primary key)
        "attempt":         0,         # increments on retry
        "enqueued_at_ms":  <int>,     # for latency metrics
    }
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from app.application.prescore.config import active_tenants_key, queue_key

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)


async def enqueue_lead(
    redis: Redis,
    *,
    tenant_id: UUID,
    lead_id: UUID,
    attempt: int = 0,
) -> None:
    """Atomic MULTI: RPUSH tenant queue + SADD active set.

    Idempotent: aynı lead_id birden fazla kez enqueue edilirse her seferinde
    yeni iş olarak sıraya girer (worker idempotency'si lead_id üzerinden DB
    write sırasında yapılır — duplicate run = aynı sonuç).

    Raises whatever Redis raises (connection error etc.) — caller (intake
    handler) best-effort try/except ile sarmaladığı varsayılır (enqueue
    başarısız olursa lead yine DB'de mevcut, manuel backfill mümkün).
    """
    tid = str(tenant_id)
    job = {
        "tenant_id": tid,
        "lead_id": str(lead_id),
        "attempt": attempt,
        "enqueued_at_ms": int(time.time() * 1000),
    }
    encoded = json.dumps(job, separators=(",", ":"))

    async with redis.pipeline(transaction=True) as pipe:
        pipe.rpush(queue_key(tid), encoded)
        pipe.sadd(active_tenants_key(), tid)
        await pipe.execute()

    log.info(
        "prescore_enqueued",
        tenant_id=tid,
        lead_id=str(lead_id),
        attempt=attempt,
    )


async def dequeue_for_tenant(
    redis: Redis,
    *,
    tenant_id: str,
) -> dict | None:
    """LPOP tenant queue. Boşsa None.

    Not: active_tenants SET'inden temizlik worker tarafında (LLEN check
    sonrası SREM) yapılır — buraya koymayız çünkü race condition olur
    (başka bir enqueue araya girerse SET'ten yanlışlıkla silebiliriz).
    """
    raw = await redis.lpop(queue_key(tenant_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("prescore_job_decode_failed", error=str(exc), raw=raw[:200])
        return None
