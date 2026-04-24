import asyncio
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from app.api.chat.router import router as chat_router
from app.api.health.router import router as health_router
from app.api.leads.router import router as leads_router
from app.api.middleware.request_id import RequestIDMiddleware
from app.api.rag.router import router as rag_router
from app.api.sessions.router import router as sessions_router
from app.api.webhook.router import router as webhook_router
from app.api.whatsapp.router import router as whatsapp_router
from app.config import get_settings
from app.infrastructure.crm.rest_client import close_crm_rest_client
from app.infrastructure.crm.webhook_client import close_crm_client
from app.infrastructure.db.pool import close_db_pool, init_db_pool
from app.infrastructure.greenapi.client import close_greenapi_client
from app.infrastructure.llm.groq_client import close_groq_client
from app.infrastructure.llm.local_llm_client import close_http_client
from app.infrastructure.redis.client import close_redis_pool, create_redis_pool
from app.interfaces.rest_public import v1_router  # Phase 1.E: public v1 API


def _configure_logging(log_level: str) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    _configure_logging(settings.log_level)

    log = structlog.get_logger(__name__)
    log.info("startup", env=settings.app_env)

    # Initialize Redis pool
    try:
        create_redis_pool(settings.redis_dsn)
        log.info("redis_pool_created", dsn=settings.redis_dsn)
    except Exception as exc:
        log.warning("redis_pool_failed", error=str(exc))

    # Initialize PostgreSQL pool
    try:
        await init_db_pool(settings.database_url)
        log.info("db_pool_created")
    except Exception as exc:
        log.warning("db_pool_failed", error=str(exc))

    # Start CRM outbox flusher
    outbox_task = asyncio.create_task(_outbox_flusher())

    # Start WhatsApp greeting worker
    greeting_task = asyncio.create_task(_greeting_worker())

    # Phase 2.M — start outbound webhook worker (in-process asyncio).
    # Tenants without outbound_webhook_url configured are silent no-ops.
    webhook_worker = None
    try:
        from app.application.webhook import WebhookWorker
        from app.infrastructure.db.pool import get_db_pool
        from app.infrastructure.redis.client import get_redis

        webhook_worker = WebhookWorker(get_redis(), get_db_pool())
        await webhook_worker.start()
    except Exception as exc:
        log.warning("webhook_worker_start_failed", error=str(exc))
        webhook_worker = None

    # Phase 3 — start pre-score worker (3-persona ensemble + OSINT + audit).
    # Gated by `PRESCORE_WORKER_ENABLED` env var. Disabled by default so the
    # worker is not auto-started in test fixtures that spin up the lifespan.
    # Flip to `true` in prod once migration 004 + OSINT stack are deployed.
    prescore_worker = None
    if settings.prescore_worker_enabled:
        try:
            from app.interfaces.rest_public.di import get_pre_score_worker

            prescore_worker = await get_pre_score_worker()
            await prescore_worker.start()
        except Exception as exc:
            log.warning("prescore_worker_start_failed", error=str(exc))
            prescore_worker = None

    yield

    # Shutdown
    log.info("shutdown_started")
    outbox_task.cancel()
    greeting_task.cancel()
    for task in (outbox_task, greeting_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    if webhook_worker is not None:
        try:
            await webhook_worker.stop()
        except Exception as exc:
            log.warning("webhook_worker_stop_failed", error=str(exc))
    if prescore_worker is not None:
        try:
            await prescore_worker.stop()
        except Exception as exc:
            log.warning("prescore_worker_stop_failed", error=str(exc))
    try:
        from app.interfaces.rest_public.di import close_osint_adapter
        await close_osint_adapter()
    except Exception as exc:
        log.warning("osint_adapter_close_failed", error=str(exc))

    await close_http_client()
    await close_groq_client()
    await close_crm_client()
    await close_crm_rest_client()
    await close_greenapi_client()
    await close_redis_pool()
    await close_db_pool()
    log.info("shutdown_complete")


async def _greeting_worker() -> None:
    """Background coroutine: process WhatsApp greeting queue sequentially."""
    import structlog as sl

    from app.application.lead_intake.greeting_worker import wa_greeting_worker
    from app.infrastructure.redis.client import get_redis
    from app.infrastructure.redis.score_repo import ScoreRepository
    from app.infrastructure.redis.session_repo import SessionRepository

    log = sl.get_logger(__name__)
    # Wait for Redis to be ready
    await asyncio.sleep(3)
    try:
        redis = get_redis()
        session_repo = SessionRepository(redis)
        score_repo = ScoreRepository(redis)
        await wa_greeting_worker(session_repo, score_repo)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("greeting_worker_crashed", error=str(exc))


async def _outbox_flusher() -> None:
    """Background coroutine: flush CRM outbox every 60 seconds."""
    import structlog as sl

    from app.infrastructure.crm.webhook_client import flush_outbox
    from app.infrastructure.redis.client import get_redis
    from app.infrastructure.redis.session_repo import SessionRepository

    log = sl.get_logger(__name__)
    while True:
        await asyncio.sleep(60)
        try:
            repo = SessionRepository(get_redis())
            sent = await flush_outbox(repo)
            if sent:
                log.info("crm_outbox_flushed", sent=sent)
        except Exception as exc:
            log.error("crm_outbox_flush_error", error=str(exc))


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="AI Lead Qualifier",
        description="Construction sector lead qualification microservice",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Correlation ID — first middleware so it wraps every other.
    app.add_middleware(RequestIDMiddleware)

    # Routers — legacy (mevcut davranış korunuyor)
    app.include_router(health_router)
    app.include_router(webhook_router)
    app.include_router(chat_router)
    app.include_router(whatsapp_router)
    app.include_router(leads_router)
    app.include_router(sessions_router)
    app.include_router(rag_router)

    # Phase 1.E — public v1 API (paralel layer, legacy'ye dokunmaz)
    app.include_router(v1_router)

    # Prometheus metrics endpoint
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    return app


app = create_app()
