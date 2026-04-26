"""
WebhookFanoutAdapter — EventPort that enqueues jobs for outbound webhook
delivery.

Outbound surface is intentionally narrow: only status-milestone events
(currently `lead.qualified`, later `lead.won` / `lead.lost` /
`lead.disqualified`) are forwarded to the tenant's configured webhook URL.
Every other event type the qualifier emits internally — `score.updated`,
`pre_score.judged`, `lead.created`, `lead.updated`, `lead.stage_changed`,
etc. — is suppressed at this adapter and never leaves the qualifier.

Body shape is fixed and minimal:

    {"event_type": "lead.qualified", "external_ref": "<partner-lead-id>"}

The partner identifies the lead by the `external_ref` they originally sent
us at intake; combined with `event_type`, it forms a deterministic
idempotency key so retries don't double-fire on the receiver.

Design notes:
    - `subscribe` raises — this adapter is publish-only. The composite
      adapter delegates live subscription to RedisPubSubAdapter.
    - Tenants without a webhook URL configured are a silent no-op.
    - Tenants can opt out per event-type via `outbound_webhook_enabled_events`.
    - `external_ref` is REQUIRED — leads created natively in our CRM (no
      partner upstream) have no one to notify, so the event is dropped.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
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


# Hard allowlist of event types that may leave the qualifier. Add to this
# set when a new status milestone needs to fan out (e.g. `lead.won`,
# `lead.lost`, `lead.disqualified`). Anything not in the set is silently
# dropped at publish time.
OUTBOUND_EVENT_ALLOWLIST: frozenset[str] = frozenset({
    "lead.qualified",
})


class WebhookFanoutAdapter(EventPort):
    def __init__(self, redis: Redis, db_pool: asyncpg.Pool) -> None:
        self._r = redis
        self._pool = db_pool

    async def publish(self, event: ScoreEvent) -> None:
        if event.event_type not in OUTBOUND_EVENT_ALLOWLIST:
            return

        external_ref = (event.external_id or "").strip()
        if not external_ref:
            log.debug(
                "webhook_skipped_no_external_ref",
                tenant_id=str(event.tenant_id),
                lead_id=str(event.lead_id),
                event_type=event.event_type,
            )
            return

        cfg = await self._tenant_webhook_config(event.tenant_id)
        if cfg is None:
            return

        patterns = cfg.get("enabled_events") or ["*"]
        if not matches(event.event_type, patterns):
            log.debug(
                "webhook_event_filtered",
                tenant_id=str(event.tenant_id),
                event_type=event.event_type,
            )
            return

        body = json.dumps(
            {"event_type": event.event_type, "external_ref": external_ref},
            separators=(",", ":"),
        )
        # Deterministic so retries (worker-internal or upstream) hit the
        # same key on the receiver. Partner dedupes on (event_type,
        # external_ref) which this id reflects.
        event_id = f"{external_ref}.{event.event_type}"
        timestamp_ms = int(event.timestamp.timestamp() * 1000)
        job = {
            "tenant_id": str(event.tenant_id),
            "event_id": event_id,
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
            event_id=event_id,
            event_type=event.event_type,
            external_ref=external_ref,
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
