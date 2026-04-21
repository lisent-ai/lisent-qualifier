"""
Tenant context middleware — asyncpg session'a `app.tenant_id` GUC set eder,
PostgreSQL RLS devreye girer.

Kullanım:
    async with tenant_db_session(db_pool, tenant_id) as conn:
        rows = await conn.fetch("SELECT * FROM qualifier_leads")
        # RLS otomatik filter eder: sadece tenant_id eşleşenler döner

Phase 1.E: Bu bir middleware yerine per-query context manager. FastAPI
middleware tüm request lifetime'ını kapsayabilir ama asyncpg Pool her query için
ayrı connection checkout yapıyor — her bir connection'da SET LOCAL zorunlu.
Bu yüzden "context manager per DB session" daha doğru pattern.

Phase 2+: super admin bypass (is_super_admin=true), tenant limits enforcement,
connection pool recycling için ALTER ROLE limits.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator
from uuid import UUID

import structlog

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger(__name__)


@asynccontextmanager
async def tenant_db_session(
    pool: asyncpg.Pool,
    tenant_id: UUID | None,
    is_super_admin: bool = False,
) -> AsyncIterator[asyncpg.Connection]:
    """
    DB connection acquire eder + RLS context'ini set eder.

    Args:
        pool: asyncpg.Pool
        tenant_id: Aktif tenant UUID (None = no tenant = silent deny on RLS'li tablolar)
        is_super_admin: RLS'i bypass et (support ops için)

    Usage:
        async with tenant_db_session(pool, tenant.id) as conn:
            leads = await conn.fetch("SELECT * FROM qualifier_leads")
    """
    async with pool.acquire() as conn:
        # Transaction içinde SET LOCAL — COMMIT/ROLLBACK sonrası auto-reset
        async with conn.transaction():
            if tenant_id is not None:
                await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            if is_super_admin:
                await conn.execute("SET LOCAL app.is_super_admin = 'true'")
            yield conn


async def set_tenant_context_on_connection(
    conn: asyncpg.Connection,
    tenant_id: UUID | None,
    is_super_admin: bool = False,
) -> None:
    """
    Mevcut bir connection üzerinde tenant context set eder (transaction dışında).

    NOT: `SET` (not `SET LOCAL`) kullanır — session-wide, connection recycle
    sırasında temizlenmeli (Pool return öncesi RESET). Pool kullanıyorsan
    `tenant_db_session` context manager'ını tercih et.
    """
    if tenant_id is not None:
        await conn.execute(f"SET app.tenant_id = '{tenant_id}'")
    if is_super_admin:
        await conn.execute("SET app.is_super_admin = 'true'")


async def reset_tenant_context_on_connection(conn: asyncpg.Connection) -> None:
    """Connection'ı pool'a geri vermeden önce tenant context'ini temizle."""
    try:
        await conn.execute("RESET app.tenant_id")
        await conn.execute("RESET app.is_super_admin")
    except Exception as exc:
        # Bazen RESET custom GUC için fail edebilir — sadece log
        log.debug("reset_tenant_context_failed", error=str(exc))
