"""qualifier_osint_profiles tablosu için module-level repository fonksiyonları.

Migration 004 ile gelen tablo — tenant-scoped (RLS), 60-day staleness check,
upsert'te refetch_count otomatik artırılır.

Kullanıcı-facing contract:
    - make_key_hash(email, phone) → sha256 hex digest (32+ char)
    - get_fresh(pool, tenant_id, key_hash, stale_days=60) → OSINTProfile | None
    - upsert(pool, tenant_id, key_hash, email, phone, profile) → None
    - touch(pool, tenant_id, key_hash) → best-effort last_used_at bump

Pattern: `app/infrastructure/db/lead_repo.py`'ye paralel (module-level async
fonksiyonlar, asyncpg.Pool ilk arg, json.dumps JSONB için).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from app.ports.osint import OSINTProfile

log = structlog.get_logger(__name__)

_NONDIGIT_RE = re.compile(r"\D+")


def make_key_hash(email: str | None, phone: str | None) -> str:
    """sha256(lower(email).strip() + '|' + digits_only(phone)).

    email + phone ikisi de None veya boş ise sabit bir "empty" anahtar döner
    (realistic case değil, ama defensive — DB'ye yine de yazılabilir).
    """
    e = (email or "").strip().lower()
    p = _NONDIGIT_RE.sub("", phone or "")
    material = f"{e}|{p}".encode()
    return hashlib.sha256(material).hexdigest()


async def get_fresh(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    key_hash: str,
    stale_days: int = 60,
) -> OSINTProfile | None:
    """60 gün içinde güncellenmiş ise profili döner, yoksa None.

    RLS: RLS middleware `SET LOCAL app.tenant_id = '<uuid>'` set etmiş olmalı.
    Test ortamında RLS yoksa (FORCE RLS'i bypass eden superuser) WHERE'de
    tenant_id explicit tutuluyor — defensive, hem prod hem test.
    """
    row = await pool.fetchrow(
        """
        SELECT profile, provider, notes, created_at, updated_at, refetch_count
        FROM qualifier_osint_profiles
        WHERE tenant_id = $1 AND key_hash = $2
        """,
        tenant_id, key_hash,
    )
    if row is None:
        return None

    updated_at: datetime = row["updated_at"]
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    age = datetime.now(UTC) - updated_at
    if age > timedelta(days=stale_days):
        log.debug(
            "osint_profile_stale",
            tenant_id=str(tenant_id),
            age_days=age.days,
        )
        return None

    profile_data = row["profile"]
    if isinstance(profile_data, str):
        profile_data = json.loads(profile_data)
    # Persisted notes kolonundaki array'i profil.notes'a üst-yaz (DB source of truth)
    profile_data["notes"] = list(row["notes"] or [])
    profile_data["provider"] = row["provider"]
    return OSINTProfile.from_dict(profile_data)


async def upsert(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    key_hash: str,
    email: str | None,
    phone: str | None,
    profile: OSINTProfile,
) -> None:
    """Insert new or update existing. Existing row'da refetch_count +1 artar."""
    await pool.execute(
        """
        INSERT INTO qualifier_osint_profiles
            (tenant_id, key_hash, email, phone, profile, provider, notes, last_used_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::text[], now())
        ON CONFLICT (tenant_id, key_hash) DO UPDATE SET
            profile       = EXCLUDED.profile,
            provider      = EXCLUDED.provider,
            notes         = EXCLUDED.notes,
            email         = COALESCE(EXCLUDED.email, qualifier_osint_profiles.email),
            phone         = COALESCE(EXCLUDED.phone, qualifier_osint_profiles.phone),
            refetch_count = qualifier_osint_profiles.refetch_count + 1,
            last_used_at  = now()
            -- updated_at: trigger tarafından otomatik set edilir
        """,
        tenant_id,
        key_hash,
        (email or None),
        (phone or None),
        json.dumps(profile.to_dict()),
        profile.provider,
        list(profile.notes),
    )
    log.debug(
        "osint_profile_upserted",
        tenant_id=str(tenant_id),
        key_hash=key_hash[:12],
        provider=profile.provider,
    )


async def touch(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    key_hash: str,
) -> None:
    """last_used_at bump — cache hit'te çağrılabilir. Best-effort (hata yutulur).

    updated_at'a dokunmaz (trigger onu updated_at'a göre yapar); ayrı kolon.
    """
    try:
        await pool.execute(
            """
            UPDATE qualifier_osint_profiles
               SET last_used_at = now()
             WHERE tenant_id = $1 AND key_hash = $2
            """,
            tenant_id, key_hash,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("osint_profile_touch_failed", error=str(exc))


async def count_by_tenant(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
) -> dict[str, Any]:
    """Observability helper — tenant başına profil sayısı + yaş dağılımı.

    MVP'de endpoint yok; admin/debug için. `tenant_id` RLS ile zaten kısıtlı,
    ama WHERE'de de explicit tutuyoruz.
    """
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE updated_at > now() - interval '10 days') AS fresh_10d,
            COUNT(*) FILTER (WHERE updated_at > now() - interval '30 days') AS fresh_30d,
            COUNT(*) FILTER (WHERE updated_at > now() - interval '60 days') AS fresh_60d,
            COALESCE(SUM(refetch_count), 0) AS total_refetches,
            MIN(created_at) AS oldest_created,
            MAX(last_used_at) AS most_recently_used
        FROM qualifier_osint_profiles
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    if row is None:
        return {
            "total": 0, "fresh_10d": 0, "fresh_30d": 0, "fresh_60d": 0,
            "total_refetches": 0, "oldest_created": None, "most_recently_used": None,
        }
    d = dict(row)
    for k in ("oldest_created", "most_recently_used"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d
