"""
PostgreSQL bağlantı havuzu — asyncpg üzerinden.

Uygulama başlarken init_db_pool() çağrılır, kapanırken close_db_pool().
get_db_pool() her yerden bağlantı havuzuna erişim sağlar.
"""
import asyncpg
import structlog

log = structlog.get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def init_db_pool(dsn: str) -> None:
    global _pool
    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    log.info("db_pool_created", dsn=dsn.split("@")[-1])  # şifre loglanmaz


async def close_db_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("db_pool_closed")


def get_db_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized. Call init_db_pool() first.")
    return _pool
