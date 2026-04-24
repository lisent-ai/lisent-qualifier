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

from app.adapters.event import (
    CompositeEventAdapter,
    RedisPubSubAdapter,
    WebhookFanoutAdapter,
)
from app.adapters.handoff import LisentCRMHandoffAdapter
from app.adapters.knowledge import LisentCRMKBAdapter, PostgresKBAdapter
from app.adapters.llm import GroqAdapter, LocalLlamaAdapter
from app.adapters.osint import (
    DBCachedOSINTAdapter,
    SelfHostedOSINTAdapter,
    StubOSINTAdapter,
)
from app.adapters.tenant import (
    CompositeTenantAdapter,
    LisentCRMTenantAdapter,
    StandaloneTenantAdapter,
)
from app.config import get_settings
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.redis.client import get_redis
from app.ports.event import EventPort
from app.ports.handoff import HandoffPort
from app.ports.knowledge import KnowledgePort
from app.ports.llm import LLMPort
from app.ports.osint import OSINTPort
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
    """Default event port — CompositeEventAdapter(RedisPubSub, WebhookFanout).

    Phase 2.M: publish fan-outs to both adapters concurrently. Failures are
    isolated — a webhook enqueue failure does not break SSE publish and vice
    versa. subscribe/replay delegate to the first (Redis) adapter.
    """
    redis = get_redis()
    pool = get_db_pool()
    return CompositeEventAdapter(
        RedisPubSubAdapter(redis),
        WebhookFanoutAdapter(redis, pool),
    )


# ============================================================================
# OSINT (Phase 3)
# ============================================================================


_osint_adapter_singleton: OSINTPort | None = None


async def get_osint_adapter() -> OSINTPort:
    """Default OSINT adapter — DBCachedOSINTAdapter with SelfHosted backend
    when `osint_enabled=true`, otherwise StubOSINTAdapter.

    Singleton: SelfHostedOSINTAdapter owns an httpx.AsyncClient which should
    be reused across requests. Lifespan handler calls `close_osint_adapter()`
    on shutdown.
    """
    global _osint_adapter_singleton
    if _osint_adapter_singleton is not None:
        return _osint_adapter_singleton

    settings = get_settings()
    if not settings.osint_enabled:
        _osint_adapter_singleton = StubOSINTAdapter()
        return _osint_adapter_singleton

    pool = get_db_pool()
    upstream = SelfHostedOSINTAdapter(
        phoneinfoga_url=settings.osint_phoneinfoga_url,
        holehe_url=settings.osint_holehe_url,
        request_timeout_s=settings.osint_request_timeout_s,
        holehe_timeout_per_module=settings.osint_holehe_timeout_per_module,
    )
    _osint_adapter_singleton = DBCachedOSINTAdapter(
        wrapped=upstream,
        pool=pool,
        stale_days=settings.osint_profile_stale_days,
    )
    return _osint_adapter_singleton


async def close_osint_adapter() -> None:
    """Release httpx client owned by SelfHostedOSINTAdapter (if any)."""
    global _osint_adapter_singleton
    if _osint_adapter_singleton is None:
        return
    # Unwrap DBCachedOSINTAdapter to find SelfHostedOSINTAdapter
    inner = getattr(_osint_adapter_singleton, "_wrapped", _osint_adapter_singleton)
    if isinstance(inner, SelfHostedOSINTAdapter):
        await inner.aclose()
    _osint_adapter_singleton = None


# ============================================================================
# Pre-Score Service & Worker (Phase 3)
# ============================================================================


async def get_pre_score_service():
    """Assemble PreScoreService with OSINT + EventPort.

    Not singleton-cached — service itself is stateless; its collaborators
    (osint adapter, event port) are already cached/reused.
    """
    from app.application.scoring.pre_score_service import PreScoreService

    osint = await get_osint_adapter()
    event_port = await get_event_port()
    settings = get_settings()
    return PreScoreService(
        osint=osint,
        event_port=event_port,
        divergence_threshold=settings.pre_score_divergence_threshold,
    )


_prescore_worker_singleton = None


async def get_pre_score_worker():
    """Assemble PreScoreWorker (singleton — owned by app lifespan)."""
    global _prescore_worker_singleton
    if _prescore_worker_singleton is not None:
        return _prescore_worker_singleton

    from app.application.prescore.worker import PreScoreWorker

    redis = get_redis()
    pool = get_db_pool()
    service = await get_pre_score_service()
    _prescore_worker_singleton = PreScoreWorker(
        redis=redis,
        db_pool=pool,
        service=service,
    )
    return _prescore_worker_singleton
