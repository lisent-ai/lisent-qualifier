"""
IngestPort — "Lead nereden geldi?"

Lead ingestion source'larını abstract eder. Webhook, REST API, widget, WhatsApp,
Phase 5/6'da MCP ve extension hepsi aynı core'u besler.

Adapters (Phase 1.D ve sonrası):
    - WebhookAdapter — mevcut `POST /webhook/lead/{token}` flow (LisentCRM path)
    - RestAPIAdapter — yeni `POST /v1/leads` flow (standalone tenant path)
    - WidgetAdapter — iframe'den gelen lead create (OAuth user context'i)
    - WhatsAppAdapter — GreenAPI'den gelen mesajlar (mevcut flow)
    - MCPToolAdapter — `qualify_lead` MCP tool call (Phase 5)
    - ExtensionAdapter — browser extension (Phase 6)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class IngestSource(str, Enum):
    """Lead'in geliş kaynağı — analytics ve routing için."""

    WEBHOOK = "webhook"
    REST_API = "rest_api"
    WIDGET = "widget"
    WHATSAPP = "whatsapp"
    MCP = "mcp"
    EXTENSION = "extension"


@dataclass
class IngestedLead:
    """Port'a teslim edilen canonical lead şekli.

    Adapter source-specific payload'u (CRM forward, widget POST, MCP tool args vs)
    bu form'a map eder. Application handler tek bir şekil bekler.
    """

    tenant_id: UUID
    source: IngestSource
    external_ref: str | None  # Upstream ID — CRM lead UUID, widget lead_id, vs
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    contact_city: str | None = None
    # Form-type fields (rule-based fit scoring için)
    project_type: str | None = None
    budget_range: str | None = None
    budget_amount: int | None = None
    timeline_urgency: str | None = None
    decision_authority: str | None = None
    # Freeform
    notes: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=datetime.utcnow)


class DuplicateLeadError(Exception):
    """Dedup key (external_ref) mevcut."""


class IngestPort(ABC):
    """Lead ingestion entry point.

    NOT: Mevcut implementation `app.application.lead_intake.ProcessWebhookLeadHandler`
    bu port'u kullanacak şekilde refactor edilecek. Webhook router doğrudan port'a
    değil, handler'a çağrı yapar; handler port'ları inject eder.
    """

    @abstractmethod
    async def accept(self, lead: IngestedLead) -> UUID:
        """Lead'i sisteme al, lead DB row'u yarat, dedup kontrolü yap.

        Returns:
            Yaratılan qualifier_leads.id (UUID).

        Raises:
            DuplicateLeadError: Aynı tenant + external_ref zaten var.
        """
