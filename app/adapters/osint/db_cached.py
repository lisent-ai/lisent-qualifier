"""DBCachedOSINTAdapter — wraps another OSINTPort with tenant-scoped DB cache.

`qualifier_osint_profiles` tablosu (Migration 004) üzerinden çalışır:
    - get_fresh(tenant, key_hash) → 60d taze ise hit
    - stale/miss → inner adapter çağrılır → upsert
    - refetch_count stale refresh'te +1 artırılır

Tenant_id zorunlu; upstream-only adapter'lardan farklı olarak burada tenant
bilinmezse exception fırlatılır (çünkü RLS key'inin bir parçası).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg
import structlog

from app.infrastructure.db import osint_profile_repo as repo
from app.ports.osint import OSINTError, OSINTPort, OSINTProfile

log = structlog.get_logger(__name__)


class DBCachedOSINTAdapter(OSINTPort):
    """Decorator pattern: bir adapter'ı DB cache'li versiyonuna dönüştürür.

    Args:
        wrapped:      Inner OSINTPort (e.g. SelfHostedOSINTAdapter). Stale/miss
                      durumunda bu çağrılır.
        pool:         asyncpg.Pool — cache lookup ve upsert için.
        stale_days:   profil bu süreden eski ise refresh (default 60).

    Cache hit davranışı:
        - `last_used_at` touch'lanır (best-effort; hata durumunda sessizce geçilir).
        - `OSINTProfile.cache_hit=True` flag'i set edilir.
        - Inner adapter ÇAĞRILMAZ.

    Cache miss / stale:
        - Inner adapter çağrılır.
        - Upsert yapılır (refetch_count stale ise +1).
        - Inner adapter exception fırlatırsa: cache YAZILMAZ, exception
          yukarı gider (caller PreScoreService stub fallback'e geçer).
    """

    def __init__(
        self,
        *,
        wrapped: OSINTPort,
        pool: asyncpg.Pool,
        stale_days: int = 60,
    ) -> None:
        self._wrapped = wrapped
        self._pool = pool
        self._stale_days = stale_days

    @property
    def name(self) -> str:
        # Dışarı aynı adapter'mış gibi görünür (provider string DB'ye yazılırken
        # inner adapter'ınki yazılır; self.name sadece logging/metrics için).
        return f"db_cached({self._wrapped.name})"

    async def enrich(
        self,
        *,
        email: str | None,
        phone: str | None,
        name: str | None = None,
        tenant_id: UUID | None = None,
    ) -> OSINTProfile:
        if tenant_id is None:
            raise OSINTError(
                "DBCachedOSINTAdapter requires tenant_id (RLS-scoped cache)",
            )
        key_hash = repo.make_key_hash(email, phone)

        cached = await repo.get_fresh(
            self._pool,
            tenant_id=tenant_id,
            key_hash=key_hash,
            stale_days=self._stale_days,
        )
        if cached is not None:
            log.debug(
                "osint_cache_hit",
                tenant_id=str(tenant_id),
                key_hash=key_hash[:12],
                provider=cached.provider,
            )
            return cached.with_cache_hit(True)

        log.debug(
            "osint_cache_miss",
            tenant_id=str(tenant_id),
            key_hash=key_hash[:12],
        )
        profile = await self._wrapped.enrich(
            email=email, phone=phone, name=name, tenant_id=tenant_id,
        )

        try:
            await repo.upsert(
                self._pool,
                tenant_id=tenant_id,
                key_hash=key_hash,
                email=email,
                phone=phone,
                profile=profile,
            )
        except Exception as exc:  # noqa: BLE001
            # Cache yazılamadı ama veri elimizde — caller'a fresh profile dön.
            log.warning(
                "osint_cache_upsert_failed",
                tenant_id=str(tenant_id),
                error=str(exc),
            )

        return profile.with_cache_hit(False)
