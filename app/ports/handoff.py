"""
HandoffPort — "Qualified lead nereye gider?"

Skoru threshold'u geçen lead'in external sisteme teslim mantığını abstract eder.
Mevcut kodda bu Lisent CRM'e webhook POST; yeni sistemde tenant-configured
outbound webhook, Slack, email, CRM, veya MCP response olabilir.

Adapters (Phase 1.D ve sonrası):
    - LisentCRMHandoffAdapter — mevcut `app/infrastructure/crm/webhook_client.py` kodu
      (circuit breaker + tenacity + Redis dead-letter outbox)
    - GenericWebhookAdapter — tenant'ın `outbound_webhook_url`'ine HMAC-signed POST
    - SlackAdapter — Slack incoming webhook'a mesaj
    - EmailAdapter — SES/Sendgrid/Resend
    - MCPResponseAdapter — MCP tool call context'inde sonucu return eder (Phase 5)

Birden fazla adapter paralel çalışabilir (fan-out).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class HandoffPayload:
    """Skorlanmış lead + reasoning — handoff sink'ine gönderilecek canonical şekil."""

    lead_id: UUID
    tenant_id: UUID
    session_id: UUID | None  # Chat path'te olur; fast path'te None
    external_ref: str | None  # Upstream sisteme dedup için (CRM lead_id, vs)
    score: int
    threshold: int
    status: str  # 'qualified', 'disqualified', 'human_takeover'
    path: str  # 'fast' | 'chat'
    framework: str  # 'champ' | 'bant' | 'meddic'
    contact: dict[str, Any] = field(default_factory=dict)
    champ: dict[str, Any] | None = None  # CHAMP breakdown + evidence (Glass Box)
    reasoning: dict[str, Any] | None = None  # Reasoning report
    score_breakdown: dict[str, Any] | None = None  # Component scores
    recommendation: dict[str, Any] | None = None  # Next action, suggested question
    cta_type: str | None = None  # 'high_touch' | 'calendly' | 'custom'
    meeting_url: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class HandoffResult:
    success: bool
    adapter_name: str
    attempted_at: str  # ISO timestamp
    error: str | None = None
    retry_queued: bool = False  # Dead-letter outbox'a eklendi mi


class HandoffPort(ABC):
    """Handoff sink — qualified lead'i dışarı teslim eder."""

    @abstractmethod
    async def send(self, payload: HandoffPayload) -> HandoffResult:
        """Lead'i teslim et. Circuit breaker + retry adapter içinde handle edilir.

        Fail durumunda HandoffResult.success=False döner, retry_queued=True ise
        background worker retry yapacak (mevcut Redis outbox pattern).
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter adı — logging + audit için. Örn. 'lisent_crm', 'generic_webhook'."""
