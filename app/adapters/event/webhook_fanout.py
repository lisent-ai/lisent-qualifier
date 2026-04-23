"""
WebhookFanoutAdapter — EventPort that enqueues jobs for outbound webhook
delivery.

This adapter runs inside the publisher's request/processing context (the
CHAMP extractor), so it MUST be fast: it looks up the tenant's webhook
config (cached), serializes the event once, and LPUSHes a job onto the
shared worker queue. Actual HTTP delivery happens on the background
`WebhookWorker`.

Design notes:
    - `subscribe` raises — this adapter is publish-only. The composite
      adapter delegates live subscription to RedisPubSubAdapter.
    - Tenants without a webhook URL configured are a silent no-op — we
      do not enqueue a job that the worker would instantly drop.
    - Event payload is the canonical ScoreEvent JSON (same shape SSE
      consumers see), so the webhook body mirrors the SSE frame.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from app.application.webhook.config import (
    TENANT_CONFIG_CACHE_TTL_SECONDS,
    queue_key,
    tenant_config_cache_key,
)
from app.ports.event import EventPort, ScoreEvent

if TYPE_CHECKING:
    import asyncpg
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

_ENQUEUE_EVENT_TYPES = frozenset({"score.updated", "lead.scored", "lead.qualified"})


class WebhookFanoutAdapter(EventPort):
    def __init__(self, redis: "Redis", db_pool: "asyncpg.Pool") -> None:
        self._r = redis
        self._pool = db_pool

    async def publish(self, event: ScoreEvent) -> None:
        if event.event_type not in _ENQUEUE_EVENT_TYPES:
            return

        has_config = await self._tenant_has_webhook(event.tenant_id)
        if not has_config:
            return

        timestamp_ms = int(event.timestamp.timestamp() * 1000)
        body = self._serialize_event(event, event_id=str(timestamp_ms))
        job = {
            "tenant_id": str(event.tenant_id),
            "event_id": str(timestamp_ms),
            "event_type": event.event_type,
            "body": body,
            "attempt": 0,
            "delivery_id": str(uuid.uuid4()),
            "first_enqueued_at_ms": timestamp_ms,
        }
        await self._r.rpush(queue_key(), json.dumps(job, separators=(",", ":")))
        log.debug(
            "webhook_enqueued",
            tenant_id=str(event.tenant_id),
            event_id=timestamp_ms,
            event_type=event.event_type,
        )

    async def subscribe(
        self,
        tenant_id: UUID,
        session_id: UUID | None = None,
    ) -> AsyncIterator[ScoreEvent]:
        raise NotImplementedError(
            "WebhookFanoutAdapter is publish-only; use RedisPubSubAdapter for subscribe"
        )
        # Make type checkers happy — this branch is unreachable at runtime.
        yield  # pragma: no cover

    # ─────────────────────────────────────────────────────────────── helpers

    async def _tenant_has_webhook(self, tenant_id: UUID) -> bool:
        """Quick check: does this tenant have a webhook URL configured?

        Shares the cache key with `WebhookWorker._resolve_tenant_config`, so
        a miss here populates the worker's cache at no extra cost. We store
        the full (url, secret) pair so the worker can reuse it without
        re-hitting the DB.
        """
        key = tenant_config_cache_key(str(tenant_id))
        try:
            raw = await self._r.get(key)
        except Exception as exc:
            log.debug("webhook_fanout_cache_read_failed", error=str(exc))
            raw = None

        if raw is not None:
            try:
                doc = json.loads(raw)
                return bool(doc.get("url") and doc.get("secret"))
            except json.JSONDecodeError:
                pass

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT outbound_webhook_url, outbound_webhook_secret FROM tenants WHERE id = $1",
                tenant_id,
            )
        url = row["outbound_webhook_url"] if row else None
        secret = row["outbound_webhook_secret"] if row else None
        has = bool(url and secret)
        try:
            await self._r.setex(
                key,
                TENANT_CONFIG_CACHE_TTL_SECONDS,
                json.dumps({"url": url if has else None, "secret": secret if has else None}),
            )
        except Exception as exc:
            log.debug("webhook_fanout_cache_write_failed", error=str(exc))
        return has

    @staticmethod
    def _serialize_event(event: ScoreEvent, *, event_id: str) -> str:
        doc = asdict(event)
        doc["tenant_id"] = str(event.tenant_id)
        doc["lead_id"] = str(event.lead_id)
        doc["session_id"] = str(event.session_id) if event.session_id else None
        doc["timestamp"] = event.timestamp.isoformat()
        doc["event_id"] = event_id
        return json.dumps(doc, separators=(",", ":"))
