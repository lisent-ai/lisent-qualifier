"""
TenantPort — "Bu çağrı kime ait? Config ne?"

Kimin çağırdığı bilgisini (ve ilgili config'i) resolve eder. Core hiçbir zaman
"Lisent CRM'den gelen bir istek" veya "external API key'li istek" ayrımını
yapmaz; TenantPort her iki durumu da `Tenant` entity'si olarak verir.

Adapters (Phase 1.D'de yazılacak):
    - LisentCRMTenantAdapter — mevcut `app/infrastructure/crm/rest_client.py`
      kodunu wrap eder (lookup_company_by_qualifier_token + config fetch)
    - StandaloneTenantAdapter — qualifier'ın kendi `tenants` tablosundan okur

Bkz:
    - docs/decisions/002-tenant-model-and-rls.md
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID


class TenantSourceType(str, Enum):
    """Tenant'ın kaynağı — qualifier hangi adapter'la çalışacağını bilir."""

    LISENT_CRM = "lisent_crm"
    STANDALONE = "standalone"
    PARTNER_SUB = "partner_sub"


class TenantPlan(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    PARTNER = "partner"
    LEGACY = "legacy"  # Mevcut CRM müşterileri için


class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIALING = "trialing"
    CHURNED = "churned"


class QualificationFramework(str, Enum):
    CHAMP = "champ"
    BANT = "bant"
    MEDDIC = "meddic"


@dataclass(frozen=True)
class Tenant:
    """Resolve edilmiş tenant. Core'un gördüğü tek tenant şekli."""

    id: UUID
    slug: str
    name: str
    source_type: TenantSourceType
    source_ref: str | None  # CRM company_id if source_type=LISENT_CRM
    plan: TenantPlan
    status: TenantStatus
    config: dict[str, Any] = field(default_factory=dict)
    domain_claims: list[str] = field(default_factory=list)
    outbound_webhook_url: str | None = None
    outbound_webhook_secret: str | None = None
    branding: dict[str, Any] = field(default_factory=dict)
    qualification_framework: QualificationFramework = QualificationFramework.CHAMP
    partner_id: UUID | None = None

    @property
    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE

    @property
    def is_legacy_crm(self) -> bool:
        """Mevcut Lisent CRM müşterisi (yeni tenant sistemine backfill'lenmiş)."""
        return self.source_type == TenantSourceType.LISENT_CRM and self.plan == TenantPlan.LEGACY


class TenantNotFoundError(Exception):
    """Token/key geçersiz veya tenant yok."""


class TenantSuspendedError(Exception):
    """Tenant suspended — istek reddedilmeli."""


class TenantPort(ABC):
    """Tenant resolve & config okuma."""

    @abstractmethod
    async def resolve_by_webhook_token(self, token: str) -> Tenant:
        """Webhook URL'deki `{token}` path param'dan tenant bul.

        Mevcut `/webhook/lead/{company_token}` endpoint'i için.
        LisentCRMTenantAdapter bunu CRM REST lookup ile yapar; StandaloneTenantAdapter
        API key hash ile yapar.

        Raises:
            TenantNotFoundError: Token geçersiz.
            TenantSuspendedError: Tenant askıda.
        """

    @abstractmethod
    async def resolve_by_api_key(self, api_key: str) -> Tenant:
        """`Authorization: Bearer sk_live_...` header'dan tenant bul.

        API key'in bcrypt hash'iyle `tenant_api_keys` tablosunda arama yapar.
        Cache'lenmeli (in-memory TTL veya Redis) çünkü her request'te çalışacak.
        """

    @abstractmethod
    async def resolve_by_id(self, tenant_id: UUID) -> Tenant:
        """Tenant ID ile direkt lookup. JWT decode sonrası kullanılır."""

    @abstractmethod
    async def resolve_by_source_ref(
        self,
        source_type: TenantSourceType,
        source_ref: str,
    ) -> Tenant | None:
        """CRM company_id → tenant lookup (legacy flow için).

        `LisentCRMTenantAdapter.resolve_by_webhook_token` internal olarak bunu çağırır.
        None döner eğer tenant yoksa (yeni company için tenant henüz yaratılmamış olabilir).
        """

    @abstractmethod
    async def get_config(self, tenant: Tenant) -> dict[str, Any]:
        """Tenant config'i (weights, thresholds, handoff_aggressiveness vs).

        LisentCRMTenantAdapter CRM'den fetch eder (`ai_config` JSONB);
        StandaloneTenantAdapter qualifier'ın kendi `tenants.config`'ini döner.

        Cache'lenebilir (1-5dk TTL önerilir).
        """

    @abstractmethod
    async def record_usage(
        self,
        tenant: Tenant,
        metric: str,
        value: int = 1,
    ) -> None:
        """Usage metric increment — Redis hot counter.

        Metrics: `leads_ingested`, `leads_qualified`, `api_calls`, `llm_tokens`,
        `champ_extractions`, `sse_sessions`, `webhook_deliveries`, `widget_loads`,
        `ask_lisent_queries`, `kb_documents`, `active_users`.

        Gün sonu background job bunu `tenant_usage` tablosuna aggregate eder.
        """
