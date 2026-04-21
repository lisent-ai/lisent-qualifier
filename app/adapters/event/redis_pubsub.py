"""
RedisPubSubAdapter — Redis PubSub üzerinden event yayını + subscription.

Mevcut `app.infrastructure.redis.score_repo.ScoreRepository` ZSET'e score
time-series yazar; bu adapter ek olarak PubSub channel'a event publish eder ve
SSE client'ları için subscription akışı sağlar.

Channel naming:
    - `tenant:{tenant_id}` — tenant-wide event'ler
    - `session:{session_id}` — session-specific (daha spesifik abone)

Format: JSON-serialized ScoreEvent dict.

Phase 1.D.5 iskele — application layer Phase 1.E'de bu adapter'a switch edilecek.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from app.ports.event import EventPort, ScoreEvent

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)


class RedisPubSubAdapter(EventPort):
    """EventPort over Redis PubSub channels."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def publish(self, event: ScoreEvent) -> None:
        """Event'i ilgili kanallara yayınla (tenant + session)."""
        payload = self._serialize(event)

        # Tenant-wide channel
        tenant_channel = f"tenant:{event.tenant_id}"
        await self._r.publish(tenant_channel, payload)

        # Session-specific channel (varsa)
        if event.session_id is not None:
            session_channel = f"session:{event.session_id}"
            await self._r.publish(session_channel, payload)
            # Score history — mevcut ScoreRepository pattern'i (ZSET)
            if event.event_type in ("lead.scored", "score.updated", "champ.extracted"):
                await self._r.zadd(
                    f"scores:{event.session_id}",
                    {str(event.score): event.timestamp.timestamp()},
                )

        log.debug(
            "event_published",
            event_type=event.event_type,
            tenant_id=str(event.tenant_id),
            session_id=str(event.session_id) if event.session_id else None,
            score=event.score,
        )

    async def subscribe(
        self,
        tenant_id: UUID,
        session_id: UUID | None = None,
    ) -> AsyncIterator[ScoreEvent]:
        """Redis PubSub subscribe async iterator.

        Usage (FastAPI SSE endpoint):
            async for event in event_port.subscribe(tenant_id, session_id):
                yield f"data: {json.dumps(event_dict)}\\n\\n"
        """
        channel = f"session:{session_id}" if session_id else f"tenant:{tenant_id}"

        pubsub = self._r.pubsub()
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue
                raw = message.get("data")
                if raw is None:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    event = self._deserialize(raw, tenant_id)
                    yield event
                except Exception as exc:
                    log.warning("redis_pubsub_decode_failed", error=str(exc), raw=raw[:200])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(event: ScoreEvent) -> str:
        """ScoreEvent → JSON string. UUID ve datetime manuel serialize edilir."""
        doc = asdict(event)
        doc["tenant_id"] = str(event.tenant_id)
        doc["lead_id"] = str(event.lead_id)
        doc["session_id"] = str(event.session_id) if event.session_id else None
        doc["timestamp"] = event.timestamp.isoformat()
        return json.dumps(doc, separators=(",", ":"))

    @staticmethod
    def _deserialize(raw: str, default_tenant_id: UUID) -> ScoreEvent:
        """JSON string → ScoreEvent."""
        doc = json.loads(raw)
        return ScoreEvent(
            tenant_id=UUID(doc.get("tenant_id", str(default_tenant_id))),
            lead_id=UUID(doc["lead_id"]),
            session_id=UUID(doc["session_id"]) if doc.get("session_id") else None,
            event_type=doc["event_type"],
            score=int(doc.get("score", 0)),
            threshold=int(doc.get("threshold", 0)),
            path=doc.get("path", ""),
            payload=doc.get("payload") or {},
            timestamp=datetime.fromisoformat(doc["timestamp"]) if doc.get("timestamp") else datetime.utcnow(),
        )
