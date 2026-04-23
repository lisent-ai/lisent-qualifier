"""
WebhookWorker — in-process asyncio consumer for outbound webhooks.

Responsibilities:
    1. Dispatch loop  — BLPOP on `webhook:queue`, deliver, route on result.
    2. Retry scheduler — poll `webhook:retry` ZSET, promote ready jobs to
       the queue.
    3. DLQ sink       — LPUSH expired jobs onto `webhook:dlq:{tenant_id}`,
       trim to the retention cap.

Job format (JSON):
    {
        "tenant_id":  "<uuid>",
        "event_id":   "<ms-int as str>",
        "event_type": "score.updated",
        "body":       "<raw JSON of the ScoreEvent>",
        "attempt":    <int, 0-indexed>,
        "delivery_id":"<uuid4>",
        "first_enqueued_at_ms": <int>
    }

URL + secret are resolved at delivery time (cached `TENANT_CONFIG_CACHE_TTL_SECONDS`)
so that a mid-flight tenant config update is respected on the next attempt.
Secrets never touch the queue.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import structlog

from app.application.webhook.config import (
    BLPOP_TIMEOUT_SECONDS,
    DELIVERY_TIMEOUT_SECONDS,
    DLQ_MAX_ENTRIES,
    MAX_ATTEMPTS,
    RETRY_DELAYS_SECONDS,
    RETRY_POLL_SECONDS,
    TENANT_CONFIG_CACHE_TTL_SECONDS,
    dlq_key,
    queue_key,
    retry_key,
    tenant_config_cache_key,
)
from app.application.webhook.delivery import deliver
from app.metrics import (
    WEBHOOK_DELIVERY_ATTEMPTS,
    WEBHOOK_DLQ_SIZE,
    WEBHOOK_QUEUE_DEPTH,
    WEBHOOK_RETRY_DEPTH,
)

if TYPE_CHECKING:
    import asyncpg
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)


@dataclass
class _TenantConfig:
    url: str
    secret: str


class WebhookWorker:
    """Lifecycle-managed worker — `start()` spawns two asyncio tasks, `stop()`
    cancels them and drains the HTTP client. Safe to `start()` once per
    process; lifespan handler in `app.main` owns the single instance."""

    def __init__(self, redis: "Redis", db_pool: "asyncpg.Pool") -> None:
        self._r = redis
        self._pool = db_pool
        self._client: httpx.AsyncClient | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._stop_event = asyncio.Event()

    # ─────────────────────────────────────────────────────────────── lifecycle

    async def start(self) -> None:
        if self._tasks:
            return  # already running
        self._client = httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_SECONDS, http2=True)
        self._stop_event.clear()
        self._tasks = [
            asyncio.create_task(self._dispatch_loop(), name="webhook-dispatch"),
            asyncio.create_task(self._retry_scheduler(), name="webhook-retry"),
            asyncio.create_task(self._metrics_refresher(), name="webhook-metrics"),
        ]
        log.info("webhook_worker_started", max_attempts=MAX_ATTEMPTS)

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
                log.warning("webhook_worker_task_crash_on_stop", error=str(exc))
        self._tasks = []
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        log.info("webhook_worker_stopped")

    # ─────────────────────────────────────────────────────────── dispatch loop

    async def _dispatch_loop(self) -> None:
        """Main consumer: BLPOP → deliver → route on result.

        BLPOP may return None (server-side timeout) OR raise a timeout-ish
        exception (client-side socket_timeout, which on the shared Redis pool
        is shorter than BLPOP_TIMEOUT_SECONDS). Both are idle-loop ticks; we
        only log true errors.
        """
        import redis.exceptions as redis_exc

        while not self._stop_event.is_set():
            try:
                result = await self._r.blpop([queue_key()], timeout=BLPOP_TIMEOUT_SECONDS)
                if result is None:
                    continue
                _, raw = result
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                await self._process_job(raw)
            except asyncio.CancelledError:
                raise
            except (redis_exc.TimeoutError, asyncio.TimeoutError, TimeoutError):
                continue  # socket timeout on BLPOP — normal idle tick
            except Exception as exc:
                log.error("webhook_dispatch_loop_error", error=str(exc))
                await asyncio.sleep(1)

    async def _process_job(self, raw: str) -> None:
        try:
            job = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("webhook_job_decode_failed", error=str(exc), raw=raw[:200])
            return

        tenant_id = job.get("tenant_id", "")
        event_id = job.get("event_id", "")
        attempt: int = int(job.get("attempt", 0))

        config = await self._resolve_tenant_config(tenant_id)
        if config is None:
            # Tenant removed webhook URL between enqueue and delivery — drop
            # silently. Not a DLQ condition; the tenant explicitly opted out.
            log.info(
                "webhook_dropped_no_config",
                tenant_id=tenant_id,
                event_id=event_id,
            )
            return

        body = job.get("body", "").encode("utf-8")
        delivery_id = job.get("delivery_id") or str(uuid.uuid4())
        event_type = job.get("event_type", "score.updated")

        outcome = await deliver(
            url=config.url,
            secret=config.secret,
            body=body,
            event_id=event_id,
            event_type=event_type,
            delivery_id=delivery_id,
            tenant_id=tenant_id,
            client=self._client,
        )

        if outcome.ok:
            WEBHOOK_DELIVERY_ATTEMPTS.labels(
                tenant_id=tenant_id, status="success"
            ).inc()
            return

        next_attempt = attempt + 1
        if next_attempt >= MAX_ATTEMPTS:
            await self._move_to_dlq(tenant_id, job, last_error=outcome.error)
            WEBHOOK_DELIVERY_ATTEMPTS.labels(
                tenant_id=tenant_id, status="dlq"
            ).inc()
            return

        await self._schedule_retry(job, next_attempt)
        WEBHOOK_DELIVERY_ATTEMPTS.labels(
            tenant_id=tenant_id, status="retry"
        ).inc()

    # ─────────────────────────────────────────────────────────── retry sched.

    async def _schedule_retry(self, job: dict, next_attempt: int) -> None:
        delay_s = RETRY_DELAYS_SECONDS[next_attempt]
        next_run_ms = int(time.time() * 1000) + delay_s * 1000
        job["attempt"] = next_attempt
        encoded = json.dumps(job, separators=(",", ":"))
        await self._r.zadd(retry_key(), {encoded: next_run_ms})
        log.info(
            "webhook_retry_scheduled",
            tenant_id=job.get("tenant_id"),
            event_id=job.get("event_id"),
            attempt=next_attempt,
            delay_s=delay_s,
        )

    async def _retry_scheduler(self) -> None:
        """Periodically move due retries back into the dispatch queue."""
        while not self._stop_event.is_set():
            try:
                now_ms = int(time.time() * 1000)
                # Atomic-ish move: fetch due items, then remove + RPUSH in a
                # pipeline. Worst-case a crash here means a retry runs twice
                # (at-least-once semantics — consumer idempotency via event_id).
                due = await self._r.zrangebyscore(
                    retry_key(), "-inf", now_ms, start=0, num=50
                )
                if due:
                    pipe = self._r.pipeline()
                    for raw in due:
                        pipe.zrem(retry_key(), raw)
                        pipe.rpush(queue_key(), raw)
                    await pipe.execute()
                    log.debug("webhook_retries_promoted", count=len(due))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("webhook_retry_scheduler_error", error=str(exc))
            await asyncio.sleep(RETRY_POLL_SECONDS)

    # ──────────────────────────────────────────────────────────── DLQ + config

    async def _move_to_dlq(
        self,
        tenant_id: str,
        job: dict,
        *,
        last_error: str | None,
    ) -> None:
        job["dlq_reason"] = last_error or "max_attempts_exhausted"
        job["dlq_at_ms"] = int(time.time() * 1000)
        encoded = json.dumps(job, separators=(",", ":"))
        key = dlq_key(tenant_id)
        pipe = self._r.pipeline()
        pipe.lpush(key, encoded)
        pipe.ltrim(key, 0, DLQ_MAX_ENTRIES - 1)
        await pipe.execute()
        log.error(
            "webhook_moved_to_dlq",
            tenant_id=tenant_id,
            event_id=job.get("event_id"),
            attempt=job.get("attempt"),
            reason=job["dlq_reason"],
        )

    async def _resolve_tenant_config(
        self, tenant_id: str
    ) -> _TenantConfig | None:
        """Cached webhook (url, secret) for a tenant.

        Miss → DB lookup → SET with TTL. On tenant config PATCH the endpoint
        invalidates this key.
        """
        key = tenant_config_cache_key(tenant_id)
        try:
            raw = await self._r.get(key)
        except Exception as exc:
            log.warning("webhook_cache_read_failed", error=str(exc))
            raw = None

        if raw is not None:
            try:
                doc = json.loads(raw)
                if doc.get("url") and doc.get("secret"):
                    return _TenantConfig(url=doc["url"], secret=doc["secret"])
                return None  # cached miss (tenant has no URL) — respect it
            except json.JSONDecodeError:
                pass  # fall through to DB

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT outbound_webhook_url, outbound_webhook_secret FROM tenants WHERE id = $1::uuid",
                tenant_id,
            )
        if not row or not row["outbound_webhook_url"] or not row["outbound_webhook_secret"]:
            # Cache the negative result briefly to avoid hammering DB
            await self._r.setex(
                key, TENANT_CONFIG_CACHE_TTL_SECONDS, json.dumps({"url": None, "secret": None})
            )
            return None

        cfg = _TenantConfig(
            url=row["outbound_webhook_url"],
            secret=row["outbound_webhook_secret"],
        )
        await self._r.setex(
            key,
            TENANT_CONFIG_CACHE_TTL_SECONDS,
            json.dumps({"url": cfg.url, "secret": cfg.secret}),
        )
        return cfg

    # ─────────────────────────────────────────────────────────────── metrics

    async def _metrics_refresher(self) -> None:
        """Low-frequency gauge update — queue/retry depth."""
        while not self._stop_event.is_set():
            try:
                q = await self._r.llen(queue_key())
                r = await self._r.zcard(retry_key())
                WEBHOOK_QUEUE_DEPTH.set(q)
                WEBHOOK_RETRY_DEPTH.set(r)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("webhook_metrics_refresh_failed", error=str(exc))
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise


async def invalidate_tenant_config_cache(redis: "Redis", tenant_id: str) -> None:
    """Called from the `/v1/config/webhook` PATCH handler on updates."""
    await redis.delete(tenant_config_cache_key(tenant_id))
