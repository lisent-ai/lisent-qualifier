"""
RedisPubSubAdapter — Redis PubSub üzerinden event yayını + subscription +
resumable replay (SSE Last-Event-ID).

Channel naming:
    - `tenant:{tenant_id}` — tenant-wide events
    - `session:{session_id}` — session-specific (daha spesifik abone)

Replay store:
    - `score_events:{session_id}` (Redis ZSET) — JSON-serialized event as the
      ZSET member, `timestamp_ms` (int) as the score. Clients that reconnect
      with `Last-Event-ID` can fetch everything since that id via
      `ZRANGEBYSCORE key (since +inf`.

Format: JSON-serialized ScoreEvent dict with `event_id` (str) + `timestamp_ms`.
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


# Match session TTL (24h) so replay store does not outlive the session.
_REPLAY_TTL_SECONDS = 86400
# Hard cap to stop the ZSET from growing unboundedly (extractions are minute-
# scale so 500 is ~weeks of history in practice).
_REPLAY_MAX_ENTRIES = 500


class RedisPubSubAdapter(EventPort):
    """EventPort over Redis PubSub channels + resumable ZSET replay store."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def publish(self, event: ScoreEvent) -> None:
        """Publish to PubSub channels + persist to the replay ZSET.

        `event.timestamp` is used as the ZSET score (milliseconds). This makes
        the replay query `ZRANGEBYSCORE score_events:{sid} (last_id +inf` an
        O(log N + M) range fetch keyed on the client-supplied Last-Event-ID.
        """
        timestamp_ms = int(event.timestamp.timestamp() * 1000)
        payload = self._serialize(event, event_id=str(timestamp_ms))

        tenant_channel = f"tenant:{event.tenant_id}"
        await self._r.publish(tenant_channel, payload)

        if event.session_id is not None:
            session_channel = f"session:{event.session_id}"
            await self._r.publish(session_channel, payload)

            if event.event_type in ("lead.scored", "score.updated", "champ.extracted"):
                replay_key = f"score_events:{event.session_id}"
                pipe = self._r.pipeline()
                pipe.zadd(replay_key, {payload: timestamp_ms})
                # Trim oldest entries once we cross the cap.
                pipe.zremrangebyrank(replay_key, 0, -(_REPLAY_MAX_ENTRIES + 1))
                pipe.expire(replay_key, _REPLAY_TTL_SECONDS)
                await pipe.execute()

        log.debug(
            "event_published",
            event_type=event.event_type,
            tenant_id=str(event.tenant_id),
            session_id=str(event.session_id) if event.session_id else None,
            score=event.score,
            event_id=timestamp_ms,
        )

    async def subscribe(
        self,
        tenant_id: UUID,
        session_id: UUID | None = None,
    ) -> AsyncIterator[ScoreEvent]:
        """Live event iterator via Redis PubSub.

        Usage (FastAPI SSE endpoint):
            async for event in event_port.subscribe(tenant_id, session_id):
                yield {"event": event.event_type, "data": ..., "id": ...}
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

    async def replay(
        self,
        session_id: UUID,
        since_event_id_ms: int = 0,
        limit: int = _REPLAY_MAX_ENTRIES,
    ) -> list[ScoreEvent]:
        """Return stored events strictly after `since_event_id_ms` (timestamp).

        Called by the SSE endpoint when the client reconnects with
        `Last-Event-ID`. Returns events ordered by timestamp (ascending), so
        the endpoint can re-emit them before flipping to the live subscription.
        """
        replay_key = f"score_events:{session_id}"
        # Exclusive lower bound: `(since` (Redis syntax for open interval).
        min_bound = f"({since_event_id_ms}" if since_event_id_ms > 0 else "-inf"
        raw_entries = await self._r.zrangebyscore(
            replay_key, min_bound, "+inf", start=0, num=limit
        )

        events: list[ScoreEvent] = []
        for raw in raw_entries:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                events.append(self._deserialize(raw, session_id))
            except Exception as exc:
                log.warning("replay_decode_failed", error=str(exc), raw=str(raw)[:200])
        return events

    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(event: ScoreEvent, *, event_id: str) -> str:
        doc = asdict(event)
        doc["tenant_id"] = str(event.tenant_id)
        doc["lead_id"] = str(event.lead_id)
        doc["session_id"] = str(event.session_id) if event.session_id else None
        doc["timestamp"] = event.timestamp.isoformat()
        doc["event_id"] = event_id
        return json.dumps(doc, separators=(",", ":"))

    @staticmethod
    def _deserialize(raw: str, default_tenant_id: UUID) -> ScoreEvent:
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
            timestamp=datetime.fromisoformat(doc["timestamp"])
            if doc.get("timestamp")
            else datetime.utcnow(),
        )
