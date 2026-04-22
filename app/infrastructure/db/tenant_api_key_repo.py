"""
tenant_api_keys tablosu üzerinden CRUD operasyonları.

Phase 2.A — API key lifecycle:
    - `insert()` — yeni key (bcrypt/sha256 hash'li) yarat
    - `find_by_hash()` — auth sırasında hash lookup (constant-time)
    - `list_by_tenant()` — admin UI için listing
    - `revoke()` — soft delete (`revoked_at` set)
    - `touch_last_used()` — auth başarılı olunca timestamp güncelle

Storage: raw key asla saklanmaz, sadece sha256 hash + last_4 (UI display için).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger(__name__)


async def insert_api_key(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    prefix: str,
    last_4: str,
    hash_hex: str,
    name: str,
    scopes: list[str],
    created_by: UUID | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """Yeni API key row yarat. Raw key asla saklanmaz."""
    row = await conn.fetchrow(
        """
        INSERT INTO tenant_api_keys (tenant_id, prefix, last_4, hash, name, scopes, created_by, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id, tenant_id, prefix, last_4, name, scopes, created_by,
                  created_at, expires_at, last_used_at, revoked_at
        """,
        tenant_id,
        prefix,
        last_4,
        hash_hex,
        name,
        scopes,
        created_by,
        expires_at,
    )
    return dict(row)


async def find_by_hash(
    conn: asyncpg.Connection,
    hash_hex: str,
) -> dict[str, Any] | None:
    """Hash ile aktif (revoke edilmemiş, expire olmamış) key bul."""
    row = await conn.fetchrow(
        """
        SELECT id, tenant_id, prefix, last_4, hash, name, scopes, created_by,
               created_at, expires_at, last_used_at, revoked_at
        FROM tenant_api_keys
        WHERE hash = $1
          AND revoked_at IS NULL
          AND (expires_at IS NULL OR expires_at > now())
        LIMIT 1
        """,
        hash_hex,
    )
    return dict(row) if row else None


async def touch_last_used(conn: asyncpg.Connection, key_id: UUID) -> None:
    """Auth başarılı olunca last_used_at güncelle. Non-critical — hata log only."""
    try:
        await conn.execute(
            "UPDATE tenant_api_keys SET last_used_at = now() WHERE id = $1",
            key_id,
        )
    except Exception as exc:  # pragma: no cover
        log.debug("touch_last_used_failed", key_id=str(key_id), error=str(exc))


async def list_by_tenant(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    *,
    include_revoked: bool = False,
) -> list[dict[str, Any]]:
    """Tenant'ın tüm (active) key'lerini listele. `hash` kolonu hariç tutulur."""
    rows = await conn.fetch(
        """
        SELECT id, tenant_id, prefix, last_4, name, scopes, created_by,
               created_at, expires_at, last_used_at, revoked_at
        FROM tenant_api_keys
        WHERE tenant_id = $1
          AND ($2::boolean OR revoked_at IS NULL)
        ORDER BY created_at DESC
        """,
        tenant_id,
        include_revoked,
    )
    return [dict(r) for r in rows]


async def revoke(conn: asyncpg.Connection, key_id: UUID, tenant_id: UUID) -> bool:
    """Key'i revoke et (soft delete). Tenant ownership kontrolü ile."""
    row = await conn.fetchrow(
        """
        UPDATE tenant_api_keys
        SET revoked_at = now()
        WHERE id = $1 AND tenant_id = $2 AND revoked_at IS NULL
        RETURNING id
        """,
        key_id,
        tenant_id,
    )
    return row is not None
