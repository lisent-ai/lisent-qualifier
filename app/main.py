import asyncio
import structlog
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from app.config import get_settings
from app.infrastructure.redis.client import create_redis_pool, close_redis_pool
from app.infrastructure.db.pool import init_db_pool, close_db_pool
from app.infrastructure.llm.local_llm_client import close_http_client
from app.infrastructure.llm.groq_client import close_groq_client
from app.infrastructure.crm.webhook_client import close_crm_client
from app.infrastructure.crm.rest_client import close_crm_rest_client
from app.infrastructure.greenapi.client import close_greenapi_client
from app.api.webhook.router import router as webhook_router
from app.api.chat.router import router as chat_router
from app.api.health.router import router as health_router
from app.api.whatsapp.router import router as whatsapp_router

import logging
import sys


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
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    _configure_logging(settings.log_level)

    log = structlog.get_logger(__name__)
    log.info("startup", env=settings.app_env)

    # Initialize Redis pool
    create_redis_pool(settings.redis_dsn)
    log.info("redis_pool_created", dsn=settings.redis_dsn)

    # Initialize PostgreSQL pool
    await init_db_pool(settings.database_url)

    # Start CRM outbox flusher
    outbox_task = asyncio.create_task(_outbox_flusher())

    yield

    # Shutdown
    log.info("shutdown_started")
    outbox_task.cancel()
    try:
        await outbox_task
    except asyncio.CancelledError:
        pass

    await close_http_client()
    await close_groq_client()
    await close_crm_client()
    await close_crm_rest_client()
    await close_greenapi_client()
    await close_redis_pool()
    await close_db_pool()
    log.info("shutdown_complete")


async def _outbox_flusher() -> None:
    """Background coroutine: flush CRM outbox every 60 seconds."""
    from app.infrastructure.redis.session_repo import SessionRepository
    from app.infrastructure.redis.client import get_redis
    from app.infrastructure.crm.webhook_client import flush_outbox
    import structlog as sl

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

    # Routers
    app.include_router(health_router)
    app.include_router(webhook_router)
    app.include_router(chat_router)
    app.include_router(whatsapp_router)

    # Prometheus metrics endpoint
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    return app


app = create_app()
