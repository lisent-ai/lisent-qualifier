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
    - Event filtering is per-tenant: the list of enabled_events (wildcard
      patterns) lives on the tenants row. Default ['*'] = everything.
    - Payload mode: 'full' embeds the canonical ScoreEvent body; 'minimal'
      strips payload detail (consumer pulls via REST). Stored per-tenant.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from app.application.webhook.config import (
    TENANT_CONFIG_CACHE_TTL_SECONDS,
    queue_key,
    tenant_config_cache_key,
)
from app.application.webhook.event_filter import matches
from app.ports.event import EventPort, ScoreEvent

if TYPE_CHECKING:
    import asyncpg
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)


class WebhookFanoutAdapter(EventPort):
    def __init__(self, redis: Redis, db_pool: asyncpg.Pool) -> None:
        self._r = redis
        self._pool = db_pool

    async def publish(self, event: ScoreEvent) -> None:
        cfg = await self._tenant_webhook_config(event.tenant_id)
        if cfg is None:
            return

        # Loop-prevention: suppress lifecycle-style echoes for leads that
        # originated in the partner CRM. If a partner pushed a lead into our
        # inbound webhook, firing `lead.created` / `lead.updated` back to the
        # same partner re-creates the source loop. Scoring events still flow
        # (`score.*`, `pre_score.*`) because partners explicitly want score
        # updates — the body carries `external_id` so they can upsert
        # instead of insert.
        if event.origin_system == "partner_intranet" and event.event_type.startswith("lead."):
            log.debug(
                "webhook_loop_suppressed",
                tenant_id=str(event.tenant_id),
                event_type=event.event_type,
                origin_system=event.origin_system,
                external_id=event.external_id,
            )
            return

        patterns = cfg.get("enabled_events") or ["*"]
        if not matches(event.event_type, patterns):
            log.debug(
                "webhook_event_filtered",
                tenant_id=str(event.tenant_id),
                event_type=event.event_type,
            )
            return

        payload_mode = cfg.get("payload_mode") or "full"

        timestamp_ms = int(event.timestamp.timestamp() * 1000)
        body = self._serialize_event(event, event_id=str(timestamp_ms), mode=payload_mode)
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
            payload_mode=payload_mode,
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

    async def _tenant_webhook_config(self, tenant_id: UUID) -> dict | None:
        """Return cached (url, secret, enabled_events, payload_mode) dict, or None.

        Returns None when the tenant has no URL+secret configured — the
        caller should no-op in that case. Shares the cache key with
        `WebhookWorker._resolve_tenant_config` so the worker sees the same
        doc shape.
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
                if doc.get("url") and doc.get("secret"):
                    return doc
                return None
            except json.JSONDecodeError:
                pass

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT outbound_webhook_url,
                       outbound_webhook_secret,
                       outbound_webhook_enabled_events,
                       outbound_webhook_payload_mode
                FROM tenants WHERE id = $1
                """,
                tenant_id,
            )
        if not row:
            return None
        url = row["outbound_webhook_url"]
        secret = row["outbound_webhook_secret"]
        if not (url and secret):
            # Cache the negative result too so we don't hit DB per event.
            try:
                await self._r.setex(
                    key,
                    TENANT_CONFIG_CACHE_TTL_SECONDS,
                    json.dumps({"url": None, "secret": None}),
                )
            except Exception as exc:
                log.debug("webhook_fanout_cache_write_failed", error=str(exc))
            return None
        enabled = list(row["outbound_webhook_enabled_events"] or ["*"])
        mode = row["outbound_webhook_payload_mode"] or "full"
        doc = {
            "url": url,
            "secret": secret,
            "enabled_events": enabled,
            "payload_mode": mode,
        }
        try:
            await self._r.setex(
                key,
                TENANT_CONFIG_CACHE_TTL_SECONDS,
                json.dumps(doc),
            )
        except Exception as exc:
            log.debug("webhook_fanout_cache_write_failed", error=str(exc))
        return doc

    @staticmethod
    def _serialize_event(event: ScoreEvent, *, event_id: str, mode: str = "full") -> str:
        doc = asdict(event)
        doc["tenant_id"] = str(event.tenant_id)
        doc["lead_id"] = str(event.lead_id)
        doc["session_id"] = str(event.session_id) if event.session_id else None
        doc["timestamp"] = event.timestamp.isoformat()
        doc["event_id"] = event_id
        # Loop-prevention metadata lives at the top level for fast upsert
        # matching on the receiver without recursing into `payload`.
        # `asdict` already emits external_id/origin_system/source — drop the
        # keys when unset to keep the envelope clean.
        for k in ("external_id", "origin_system", "source"):
            if doc.get(k) is None:
                doc.pop(k, None)
        if mode == "minimal":
            # Strip the nested payload dict — the consumer can pull detail
            # from the REST API using (tenant_id, lead_id). Top-level
            # metadata (score, threshold, event_type) is kept so consumers
            # can route without a secondary fetch.
            doc["payload"] = {}
            doc["payload_mode"] = "minimal"
        else:
            doc["payload_mode"] = "full"
        return json.dumps(doc, separators=(",", ":"))
