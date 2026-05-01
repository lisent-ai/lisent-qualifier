"""
WebhookFanoutAdapter — EventPort that enqueues jobs for outbound webhook
delivery.

Outbound surface covers every pipeline status milestone — `lead.qualified`,
`lead.won`, `lead.lost`, `lead.disqualified`, and the umbrella
`lead.stage_changed` — so a partner's CRM webhook fires on every stage
transition in real time. Pre-pipeline events (`score.updated`,
`pre_score.judged`, `lead.created`, `lead.updated`) stay internal.

Body shape is driven by the tenant's `outbound_webhook_payload_mode`:

    minimal:
        {"event_type": "lead.stage_changed", "external_id": "<id>"}

    full (default):
        {
          "event_type":   "lead.stage_changed",
          "external_id":  "<partner-lead-id-or-fallback>",
          "tenant_id":    "<uuid>",
          "lead_id":      "<uuid>",
          "occurred_at":  "<iso-8601-utc>",
          "from_stage":   "contacted",
          "to_stage":     "converted",
          "changed_by":   "<actor-or-null>",
          "score":        <int|null>,
          "lead":         { ...full CRM lead snapshot... },
          "test":         <true on synthetic test events, omitted otherwise>
        }

`external_id` falls back to the qualifier `lead_id` when the lead has no
partner upstream identifier, so native CRM leads still trigger webhooks
(the partner can still address them by a stable id). The field name
matches the CRM `leads.external_id` column and the qualifier's
`ScoreEvent.external_id` so the same identifier flows end-to-end under
one name.

Design notes:
    - `subscribe` raises — this adapter is publish-only. The composite
      adapter delegates live subscription to RedisPubSubAdapter.
    - Tenants without a webhook URL configured are a silent no-op.
    - Tenants can opt out per event-type via `outbound_webhook_enabled_events`.
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


# Hard allowlist of event types that may leave the qualifier. Pipeline
# milestones plus the umbrella `lead.stage_changed` so partners can react
# to every CRM transition. Pre-pipeline events (score.updated,
# pre_score.judged, lead.created/updated) stay internal.
OUTBOUND_EVENT_ALLOWLIST: frozenset[str] = frozenset({
    "lead.stage_changed",
    "lead.qualified",
    "lead.won",
    "lead.lost",
    "lead.disqualified",
})


class WebhookFanoutAdapter(EventPort):
    def __init__(self, redis: Redis, db_pool: asyncpg.Pool) -> None:
        self._r = redis
        self._pool = db_pool

    async def publish(self, event: ScoreEvent) -> None:
        if event.event_type not in OUTBOUND_EVENT_ALLOWLIST:
            return

        # Prefer the partner's external_id; fall back to our qualifier
        # lead_id so native CRM leads still notify the partner. The
        # receiver can disambiguate via the `origin_system` field below.
        external_id = (event.external_id or "").strip() or str(event.lead_id)

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

        payload_mode = (cfg.get("payload_mode") or "full").lower()
        body_doc: dict = {
            "event_type": event.event_type,
            "external_id": external_id,
        }
        if payload_mode != "minimal":
            ev_payload = event.payload or {}
            body_doc.update({
                "tenant_id": str(event.tenant_id),
                "lead_id": str(event.lead_id),
                "occurred_at": event.timestamp.isoformat() + "Z",
                "from_stage": ev_payload.get("from_stage"),
                "to_stage": ev_payload.get("to_stage"),
                "changed_by": ev_payload.get("changed_by"),
                "score": event.score if event.score else None,
                "origin_system": event.origin_system,
                "source": event.source,
                "lead": ev_payload.get("lead") or {},
            })
            if ev_payload.get("test"):
                body_doc["test"] = True
        body = json.dumps(body_doc, separators=(",", ":"), default=str)
        # Deterministic so retries (worker-internal or upstream) hit the
        # same key on the receiver. Partner dedupes on (event_type,
        # external_id, occurred_at_ms) which this id reflects.
        timestamp_ms = int(event.timestamp.timestamp() * 1000)
        event_id = f"{external_id}.{event.event_type}.{timestamp_ms}"
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
            external_id=external_id,
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
