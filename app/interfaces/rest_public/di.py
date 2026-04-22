"""
Dependency injection factory — port → concrete adapter.

FastAPI Depends() fonksiyonlarını tanımlar. Handler'lar port alır, burada
gerçek adapter üretilir. Phase 1.E'de LisentCRMTenantAdapter default —
sonrasında environment/tenant'a göre composite adapter'lar eklenecek.

Usage:
    @router.get("/leads")
    async def list_leads(tenant_adapter: TenantPort = Depends(get_tenant_adapter)):
        ...
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.adapters.event import RedisPubSubAdapter
from app.adapters.handoff import LisentCRMHandoffAdapter
from app.adapters.knowledge import LisentCRMKBAdapter, PostgresKBAdapter
from app.adapters.llm import GroqAdapter, LocalLlamaAdapter
from app.adapters.tenant import (
    CompositeTenantAdapter,
    LisentCRMTenantAdapter,
    StandaloneTenantAdapter,
)
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.redis.client import get_redis
from app.ports.event import EventPort
from app.ports.handoff import HandoffPort
from app.ports.knowledge import KnowledgePort
from app.ports.llm import LLMPort
from app.ports.tenant import TenantPort

log = structlog.get_logger(__name__)


# ============================================================================
# Tenant
# ============================================================================


async def get_tenant_adapter() -> TenantPort:
    """Default tenant adapter — CompositeTenantAdapter.

    Chain: önce StandaloneTenantAdapter (yeni external müşteriler, slug/API key
    lookup qualifier DB'de); yoksa LisentCRMTenantAdapter'a düşer (legacy CRM
    company lookup).
    """
    pool = get_db_pool()
    return CompositeTenantAdapter(
        primary=StandaloneTenantAdapter(db_pool=pool),
        fallback=LisentCRMTenantAdapter(db_pool=pool),
    )


async def get_standalone_tenant_adapter() -> TenantPort:
    """Sadece standalone tenant lookup (explicit endpoint'ler için)."""
    pool = get_db_pool()
    return StandaloneTenantAdapter(db_pool=pool)


# ============================================================================
# Handoff
# ============================================================================


async def get_handoff_adapter() -> HandoffPort:
    """Default handoff: Lisent CRM webhook. Tenant'a göre `GenericWebhookAdapter`
    routing'i Phase 1.F'de composite'le gelir."""
    return LisentCRMHandoffAdapter()


# ============================================================================
# LLM
# ============================================================================


@lru_cache(maxsize=1)
def get_groq_adapter() -> LLMPort:
    """Groq singleton (connection pool reuse)."""
    return GroqAdapter()


@lru_cache(maxsize=1)
def get_local_llama_adapter() -> LLMPort:
    """Local llama singleton."""
    return LocalLlamaAdapter()


# ============================================================================
# Knowledge
# ============================================================================


async def get_kb_adapter_postgres() -> KnowledgePort:
    return PostgresKBAdapter()


async def get_kb_adapter_lisent_crm() -> KnowledgePort:
    """LisentCRMKBAdapter for legacy tenants."""
    tenant_adapter = await get_tenant_adapter()
    return LisentCRMKBAdapter(tenant_resolver=tenant_adapter)


# ============================================================================
# Event
# ============================================================================


async def get_event_port() -> EventPort:
    """Default event port — RedisPubSubAdapter over the shared Redis client.

    Phase 2.D uses this single adapter for SSE publish+replay. In Phase 2.M a
    CompositeEventAdapter chains the webhook fanout adapter here, so publishers
    stay untouched when outbound webhooks land.
    """
    return RedisPubSubAdapter(get_redis())
