"""
CompositeEventAdapter — fan out a ScoreEvent across N EventPort adapters.

`publish` runs all adapters concurrently; failures in one adapter are
logged but do not block the others (consumers are independently
available). `subscribe` / `replay` delegate to the FIRST adapter in the
chain — conventionally the PubSub/replay adapter (RedisPubSubAdapter)
ranks ahead of write-only adapters (WebhookFanoutAdapter).

Phase 2.M uses:
    CompositeEventAdapter(RedisPubSubAdapter, WebhookFanoutAdapter)
                          └─ primary (publish+subscribe+replay)
                                     └─ secondary (publish-only fanout)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import structlog

from app.ports.event import EventPort, ScoreEvent

log = structlog.get_logger(__name__)


class CompositeEventAdapter(EventPort):
    def __init__(self, *adapters: EventPort) -> None:
        if not adapters:
            raise ValueError("CompositeEventAdapter requires at least one adapter")
        self._adapters = adapters

    async def publish(self, event: ScoreEvent) -> None:
        results = await asyncio.gather(
            *(a.publish(event) for a in self._adapters),
            return_exceptions=True,
        )
        for adapter, result in zip(self._adapters, results, strict=True):
            if isinstance(result, Exception):
                log.warning(
                    "composite_adapter_publish_failed",
                    adapter=type(adapter).__name__,
                    error=str(result),
                    event_type=event.event_type,
                    tenant_id=str(event.tenant_id),
                )

    async def subscribe(
        self,
        tenant_id: UUID,
        session_id: UUID | None = None,
    ) -> AsyncIterator[ScoreEvent]:
        """Delegate to the first adapter — PubSub owner."""
        async for event in self._adapters[0].subscribe(tenant_id, session_id):
            yield event

    async def replay(
        self,
        session_id: UUID,
        since_event_id_ms: int = 0,
        limit: int | None = None,
    ) -> list[ScoreEvent]:
        """Delegate replay to the first adapter that implements it."""
        for adapter in self._adapters:
            replay_fn = getattr(adapter, "replay", None)
            if replay_fn is not None:
                if limit is None:
                    return await replay_fn(session_id, since_event_id_ms)
                return await replay_fn(session_id, since_event_id_ms, limit)
        return []
