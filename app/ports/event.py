"""
EventPort — "Event'ler nereye yayılır?"

Score update'leri (SSE streaming), session stage transitions, audit events.
Mevcut implementasyon Redis PubSub; yeni mimari aynı port arkasında fan-out
pattern'ine izin verir (SSE broadcast + outbound webhooks + audit log).

Adapters (Phase 1.D):
    - RedisPubSubAdapter — mevcut `app/infrastructure/redis/*` kodu
      (session:{id}, scores:{id}, outbox)
    - SSEBroadcastAdapter — score stream'ini SSE client'larına push eder
    - WebhookFanoutAdapter — tenant'ın outbound webhook'larına event fan-out
      (HMAC-signed + retry + dead-letter)

Birden fazla adapter chain'lenebilir (composite pattern).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

# Canonical registry of event types the qualifier knows how to emit or
# forward. Grouped by category so the admin UI can present checkboxes.
# Keep category labels stable — the frontend keys off them.
EVENT_CATALOG: dict[str, list[str]] = {
    "scoring": ["score.updated", "pre_score.judged", "lead.scored"],
    "lifecycle": ["lead.created", "lead.updated", "lead.qualified"],
    "pipeline": [
        "lead.stage_changed",
        "lead.won",
        "lead.lost",
        "lead.disqualified",
    ],
}

EVENT_TYPES: frozenset[str] = frozenset(e for group in EVENT_CATALOG.values() for e in group)


@dataclass
class ScoreEvent:
    """Skor update event — SSE + audit için canonical şekil."""

    tenant_id: UUID
    lead_id: UUID
    session_id: UUID | None
    event_type: str  # 'lead.scored', 'champ.extracted', 'lead.qualified', 'score.updated'
    score: int
    threshold: int
    path: str  # 'fast' | 'chat'
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


class EventPort(ABC):
    """Event pub/sub — SSE + webhook + audit."""

    @abstractmethod
    async def publish(self, event: ScoreEvent) -> None:
        """Event'i yayınla. Adapter'a bağlı: Redis PubSub channel'a push,
        SSE broadcast queue'ya ekle, veya webhook fan-out tetikle."""

    @abstractmethod
    async def subscribe(
        self,
        tenant_id: UUID,
        session_id: UUID | None = None,
    ):
        """SSE stream için async iterator döner.

        Usage:
            async for event in event_port.subscribe(tenant_id, session_id):
                yield f"data: {event.to_json()}\\n\\n"

        session_id verilirse sadece o session'un event'leri; None ise tenant-level.
        """
