"""PreScoreWorker — in-process asyncio consumer for pre-scoring jobs.

Responsibilities:
    1. Dispatch loop  — SRANDMEMBER active_tenants → LPOP tenant queue → process
    2. Retry scheduler — ZSET `prescore:retry` poll; due items → re-enqueue
    3. DLQ sink       — 3 failed attempts → LPUSH prescore:dlq:{tid}
    4. Metrics        — queue depth per tenant (gauge), retry depth

RR fairness: SRANDMEMBER istatistiksel adil — 3+ active tenant olduğunda
dağılım yakın dengeli olur. Strict RR gerekirse ZSET+cursor ile upgrade
edilebilir.

Throttle: her iş arasında 500-1500ms jittered sleep (global OSINT + Groq
rate limitini ezmemek için). Multi-worker concurrency'de her worker kendi
jitter'ıyla girer, kümülatif rate Groq/OSINT upstream rate limiter
tarafında kısıtlanır.

Lifecycle: `start()` spawns dispatch + retry_scheduler + metrics_refresher
asyncio task'ları. `stop()` cancel + drain. Tek worker default;
`prescore_worker_concurrency>1` için ayrı worker instance'ları gerekir
(shared Redis, aynı RR poll).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import TYPE_CHECKING

import structlog

from app.application.prescore.config import (
    DLQ_MAX_ENTRIES,
    IDLE_SLEEP_SECONDS,
    MAX_ATTEMPTS,
    RETRY_DELAYS_SECONDS,
    RETRY_POLL_SECONDS,
    THROTTLE_MAX_S,
    THROTTLE_MIN_S,
    active_tenants_key,
    dlq_key,
    queue_key,
    retry_key,
)
from app.application.prescore.queue import dequeue_for_tenant

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg
    from redis.asyncio import Redis

    from app.application.scoring.pre_score_service import PreScoreService

log = structlog.get_logger(__name__)


class PreScoreWorker:
    """Lifecycle-managed worker.

    Args:
        redis:       Redis client (aynı pool diğer subsystem'lerle paylaşılır).
        db_pool:     asyncpg.Pool — her iş için kısa-ömürlü connection.
        service:     PreScoreService — enrichment + scoring + writeback.
        tenant_resolver: Callable[[UUID], Awaitable[(icp, sector, threshold)]]
                         — tenant'ın ICP + sector + qualification_threshold'unu
                         döndürür. DI ile verilir.
    """

    def __init__(
        self,
        *,
        redis: Redis,
        db_pool: asyncpg.Pool,
        service: PreScoreService,
        tenant_resolver=None,  # async fn (UUID) -> (icp: str, sector: str, threshold: int)
    ) -> None:
        self._r = redis
        self._pool = db_pool
        self._service = service
        self._resolve_tenant = tenant_resolver or _default_tenant_resolver
        self._tasks: list[asyncio.Task[None]] = []
        self._stop_event = asyncio.Event()

    # ─────────────────────────────────────────────────────────── lifecycle

    async def start(self) -> None:
        if self._tasks:
            return
        self._stop_event.clear()
        self._tasks = [
            asyncio.create_task(self._dispatch_loop(), name="prescore-dispatch"),
            asyncio.create_task(self._retry_scheduler(), name="prescore-retry"),
            asyncio.create_task(self._metrics_refresher(), name="prescore-metrics"),
            asyncio.create_task(self._sweeper_loop(), name="prescore-sweeper"),
        ]
        log.info("prescore_worker_started", max_attempts=MAX_ATTEMPTS)

    async def stop(self) -> None:
        if not self._tasks:
            return
        self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.warning("prescore_worker_task_crash_on_stop", error=str(exc))
        self._tasks = []
        log.info("prescore_worker_stopped")

    # ─────────────────────────────────────────────────────────── dispatch

    async def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                tenant_id = await self._pick_active_tenant()
                if tenant_id is None:
                    # No work. Brief sleep so we don't busy-spin.
                    await self._sleep(IDLE_SLEEP_SECONDS)
                    continue

                job = await dequeue_for_tenant(self._r, tenant_id=tenant_id)
                if job is None:
                    # Tenant queue empty — remove from active set (best-effort).
                    # Race: another enqueue could re-add; that's fine.
                    remaining = await self._r.llen(queue_key(tenant_id))
                    if remaining == 0:
                        await self._r.srem(active_tenants_key(), tenant_id)
                    continue

                # Jittered throttle BEFORE processing (avoids burst on upstream).
                await self._sleep(random.uniform(THROTTLE_MIN_S, THROTTLE_MAX_S))

                await self._process_job(job)

                # Post-process: if tenant queue now empty, remove from active set.
                remaining = await self._r.llen(queue_key(tenant_id))
                if remaining == 0:
                    await self._r.srem(active_tenants_key(), tenant_id)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("prescore_dispatch_loop_error", error=str(exc))
                await self._sleep(1.0)

    async def _pick_active_tenant(self) -> str | None:
        """SRANDMEMBER active_tenants — statistical RR across tenants."""
        try:
            tid = await self._r.srandmember(active_tenants_key())
        except Exception as exc:
            log.warning("prescore_srandmember_failed", error=str(exc))
            return None
        if tid is None:
            return None
        return tid.decode("utf-8") if isinstance(tid, bytes) else str(tid)

    async def _process_job(self, job: dict) -> None:
        from uuid import UUID as _UUID

        tenant_id_str = job.get("tenant_id", "")
        lead_id_str = job.get("lead_id", "")
        attempt = int(job.get("attempt", 0))

        try:
            tenant_id = _UUID(tenant_id_str)
            lead_id = _UUID(lead_id_str)
        except (ValueError, TypeError) as exc:
            log.warning("prescore_job_invalid_uuids", job=job, error=str(exc))
            return  # malformed — drop silently, can't retry sensibly

        try:
            icp, sector, threshold = await self._resolve_tenant(self._pool, tenant_id)
        except Exception as exc:
            log.warning(
                "prescore_tenant_resolve_failed",
                tenant_id=tenant_id_str,
                error=str(exc),
            )
            await self._handle_failure(job, attempt, reason=f"tenant_resolve: {exc}")
            return

        try:
            async with self._pool.acquire() as conn:
                # RLS: SET LOCAL app.tenant_id (RLS için)
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    tenant_id_str,
                )
                await self._service.score(
                    tenant_id=tenant_id,
                    lead_id=lead_id,
                    conn=conn,
                    ideal_customer_profile=icp,
                    sector=sector,
                    qualification_threshold=threshold,
                    attempt=attempt,
                    # On the last retry slot, collapse ensemble failure to
                    # the data-quality fallback so the lead row is unlocked.
                    is_final_attempt=(attempt + 1 >= MAX_ATTEMPTS),
                )
        except Exception as exc:
            log.warning(
                "prescore_job_processing_failed",
                tenant_id=tenant_id_str,
                lead_id=lead_id_str,
                attempt=attempt,
                error=f"{type(exc).__name__}: {exc}",
            )
            await self._handle_failure(
                job, attempt, reason=f"{type(exc).__name__}: {str(exc)[:200]}",
            )

    async def _handle_failure(self, job: dict, attempt: int, *, reason: str) -> None:
        next_attempt = attempt + 1
        if next_attempt >= MAX_ATTEMPTS:
            await self._move_to_dlq(job, last_error=reason)
            return
        await self._schedule_retry(job, next_attempt)

    # ─────────────────────────────────────────────────────────── retry

    async def _schedule_retry(self, job: dict, next_attempt: int) -> None:
        # RETRY_DELAYS_SECONDS index'i: next_attempt-1 (0-indexed: 1st retry → index 0 = 30s)
        delay_idx = min(next_attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)
        delay_s = RETRY_DELAYS_SECONDS[delay_idx]
        next_run_ms = int(time.time() * 1000) + delay_s * 1000
        job["attempt"] = next_attempt
        encoded = json.dumps(job, separators=(",", ":"))
        await self._r.zadd(retry_key(), {encoded: next_run_ms})
        log.info(
            "prescore_retry_scheduled",
            tenant_id=job.get("tenant_id"),
            lead_id=job.get("lead_id"),
            attempt=next_attempt,
            delay_s=delay_s,
        )

    async def _retry_scheduler(self) -> None:
        """Poll retry ZSET, promote due items back to their tenant queue."""
        while not self._stop_event.is_set():
            try:
                now_ms = int(time.time() * 1000)
                due = await self._r.zrangebyscore(
                    retry_key(), "-inf", now_ms, start=0, num=50,
                )
                if due:
                    pipe = self._r.pipeline(transaction=False)
                    for raw in due:
                        raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                        try:
                            job = json.loads(raw_str)
                        except json.JSONDecodeError:
                            # Corrupt retry entry — remove, don't re-queue
                            pipe.zrem(retry_key(), raw)
                            continue
                        tid = job.get("tenant_id", "")
                        if not tid:
                            pipe.zrem(retry_key(), raw)
                            continue
                        pipe.zrem(retry_key(), raw)
                        pipe.rpush(queue_key(tid), raw_str)
                        pipe.sadd(active_tenants_key(), tid)
                    await pipe.execute()
                    log.debug("prescore_retries_promoted", count=len(due))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("prescore_retry_scheduler_error", error=str(exc))
            await self._sleep(RETRY_POLL_SECONDS)

    # ─────────────────────────────────────────────────────────── DLQ

    async def _move_to_dlq(self, job: dict, *, last_error: str) -> None:
        tenant_id = job.get("tenant_id", "")
        job["dlq_reason"] = last_error
        job["dlq_at_ms"] = int(time.time() * 1000)
        encoded = json.dumps(job, separators=(",", ":"))
        key = dlq_key(tenant_id)
        pipe = self._r.pipeline()
        pipe.lpush(key, encoded)
        pipe.ltrim(key, 0, DLQ_MAX_ENTRIES - 1)
        await pipe.execute()
        log.error(
            "prescore_moved_to_dlq",
            tenant_id=tenant_id,
            lead_id=job.get("lead_id"),
            attempt=job.get("attempt"),
            reason=last_error,
        )

    # ─────────────────────────────────────────────────────────── metrics

    async def _metrics_refresher(self) -> None:
        """Gauge refresh — active tenant count, per-tenant queue depth."""
        while not self._stop_event.is_set():
            try:
                active_count = await self._r.scard(active_tenants_key())
                retry_count = await self._r.zcard(retry_key())
                try:
                    from app.metrics import (
                        PRESCORE_ACTIVE_TENANTS,
                        PRESCORE_RETRY_DEPTH,
                    )
                    PRESCORE_ACTIVE_TENANTS.set(active_count)
                    PRESCORE_RETRY_DEPTH.set(retry_count)
                except ImportError:
                    # Metrics tanımlanmamışsa (Phase 4'te eklenecek) sessizce geç
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("prescore_metrics_refresh_failed", error=str(exc))
            await self._sleep(10.0)

    # ─────────────────────────────────────────────────────────── sweeper

    async def _sweeper_loop(self) -> None:
        """Reconcile leads that were stamped `enqueue_failed=true` by the
        router (Redis RPUSH hiccup). Re-enqueue so the pipeline makes
        progress without operator action.

        Design note: we do NOT sweep silent orphans (score=0 with no
        flag). Earlier iteration did — it turned into a feedback loop
        because the same rows re-surfaced every cycle faster than the
        worker could drain them, causing the queue to grow unboundedly.
        A real orphan without the flag is an operational concern that
        needs human review, not automatic retry.

        Re-enqueue guard: if a lead was already requeued less than
        `REQUEUE_COOLDOWN_MIN` ago, skip it. Lets the worker finish
        current attempts before we pile on more.
        """
        from app.application.prescore.queue import enqueue_lead

        SWEEP_INTERVAL_S = 60.0
        GRACE_MINUTES = 5
        # Minimum gap between re-enqueues of the same lead — gives the
        # worker time to drain.
        REQUEUE_COOLDOWN_MIN = 15
        BATCH_SIZE = 10

        while not self._stop_event.is_set():
            await self._sleep(SWEEP_INTERVAL_S)
            if self._stop_event.is_set():
                return
            try:
                async with self._pool.acquire() as conn:
                    # Superuser RLS bypass: sweeper works across tenants.
                    await conn.execute(
                        "SELECT set_config('app.super_admin_bypass', 'true', true)",
                    )
                    rows = await conn.fetch(
                        f"""
                        SELECT id, tenant_id
                        FROM qualifier_leads
                        WHERE score = 0
                          AND tenant_id IS NOT NULL
                          AND created_at < NOW() - INTERVAL '{GRACE_MINUTES} minutes'
                          -- Only act on leads the router explicitly marked.
                          AND (
                                (extra_data->>'enqueue_failed')::boolean IS TRUE
                             OR extra_data->>'enqueue_failed' = 'true'
                          )
                          -- Cooldown: don't pile on if we already retried
                          -- within the window.
                          AND (
                                extra_data->>'reenqueued_at' IS NULL
                             OR (extra_data->>'reenqueued_at')::timestamptz
                                  < NOW() - INTERVAL '{REQUEUE_COOLDOWN_MIN} minutes'
                          )
                        ORDER BY created_at ASC
                        LIMIT {BATCH_SIZE}
                        """,
                    )
                if not rows:
                    continue
                requeued = 0
                for row in rows:
                    try:
                        await enqueue_lead(
                            self._r,
                            tenant_id=row["tenant_id"],
                            lead_id=row["id"],
                        )
                        # Clear the flag so we don't re-pick next cycle.
                        async with self._pool.acquire() as conn2:
                            await conn2.execute(
                                "SELECT set_config('app.super_admin_bypass', 'true', true)",
                            )
                            await conn2.execute(
                                """
                                UPDATE qualifier_leads
                                   SET extra_data = COALESCE(extra_data, '{}'::jsonb)
                                                   - 'enqueue_failed'
                                                   - 'enqueue_failed_at'
                                                   || jsonb_build_object(
                                                        'reenqueued_at',
                                                        to_char(NOW() AT TIME ZONE 'UTC',
                                                                'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                                                      )
                                 WHERE id = $1::uuid
                                """,
                                str(row["id"]),
                            )
                        requeued += 1
                    except Exception as exc:
                        log.warning(
                            "prescore_sweeper_requeue_failed",
                            lead_id=str(row["id"]),
                            error=f"{type(exc).__name__}: {exc}",
                        )
                if requeued:
                    log.info(
                        "prescore_sweeper_requeued",
                        count=requeued,
                        scanned=len(rows),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("prescore_sweeper_error", error=str(exc))

    # ─────────────────────────────────────────────────────────── helpers

    async def _sleep(self, seconds: float) -> None:
        """CancelledError-aware sleep."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            return  # normal — sleep completed
        # If we got here, stop_event was set during sleep — upstream loop
        # will exit on next iteration.


async def _default_tenant_resolver(
    pool: asyncpg.Pool,
    tenant_id: UUID,
) -> tuple[str, str, int]:
    """Fallback resolver: tenants.config'den ICP + sector + threshold oku.

    Production'da DI ile TenantPort adapter'ı kullanılması tercih edilir;
    bu çok temel fallback — gerçek tenant resolution cache'lenmeli.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT config, qualification_framework
            FROM tenants
            WHERE id = $1::uuid
            """,
            str(tenant_id),
        )
    if row is None:
        return "", "construction", 75
    config = row["config"] or {}
    if isinstance(config, str):
        config = json.loads(config)
    icp = config.get("ideal_customer_profile") or ""
    sector = config.get("industry_focus") or "construction"
    threshold = int(config.get("qualification_threshold") or 75)
    return icp, sector, threshold
