"""
StandaloneTenantAdapter — Qualifier'ın kendi `tenants` tablosu üzerinden çalışan
TenantPort implementasyonu. External müşteriler (CRM dışı) için.

Lookup kaynakları:
    - Webhook token: `tenant_api_keys` (eş bcrypt hash)
    - API key: `tenant_api_keys` (aynı)
    - ID: `tenants` tablosu direkt
    - Source ref: `tenants` tablosu (source_type='standalone' veya 'partner_sub')
    - Config: `tenants.config` JSONB

Phase 1.D.1: Skeleton — DB query yapıları kuruldu, API key hashing Phase 1.E'de
(bcrypt hash + last_used_at update). Şu an webhook token lookup API key ile aynı
(prod'da farklılaşacak).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from app.ports.tenant import (
    QualificationFramework,
    Tenant,
    TenantNotFoundError,
    TenantPlan,
    TenantPort,
    TenantSourceType,
    TenantStatus,
    TenantSuspendedError,
)

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger(__name__)


class StandaloneTenantAdapter(TenantPort):
    """TenantPort over qualifier DB tenants table."""

    def __init__(self, db_pool: asyncpg.Pool) -> None:
        self._pool = db_pool

    async def resolve_by_webhook_token(self, token: str) -> Tenant:
        """
        Webhook token → Tenant lookup.

        Phase 1.E (current): slug-based lookup — dev/test için basit auth.
        Token tenant.slug'ıyla eşleşir (örn. 'acme-insaat').

        Phase 2: bcrypt hash lookup via `tenant_api_keys` tablosu.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tenants WHERE slug = $1 AND status = 'active'",
                token,
            )
        if row is None:
            raise TenantNotFoundError(f"No active tenant with slug={token!r}")

        tenant = self._row_to_tenant(row)
        if tenant.status == TenantStatus.SUSPENDED:
            raise TenantSuspendedError(f"tenant {tenant.slug} suspended")
        return tenant

    async def resolve_by_api_key(self, api_key: str) -> Tenant:
        """
        API key → Tenant lookup (Phase 2.A).

        Flow:
            1. SHA-256 hash hesapla (pepper ile)
            2. `tenant_api_keys` tablosunda active (revoked_at IS NULL +
               expires_at > now()) hash eşleşmesi ara
            3. Bulursan tenant lookup + status check + last_used_at update
            4. Bulamazsan TenantNotFoundError

        Scope'lar Tenant'a yüklenmez — endpoint-level FastAPI Depends ile
        kontrol edilir (ayrı dependency: `require_scope("lead:write")`).
        """
        from app.config import get_settings
        from app.domain.tenant.api_key import hash_api_key
        from app.infrastructure.db import tenant_api_key_repo

        settings = get_settings()
        hash_hex = hash_api_key(api_key, pepper=settings.api_key_pepper)

        async with self._pool.acquire() as conn:
            key_record = await tenant_api_key_repo.find_by_hash(conn, hash_hex)
            if key_record is None:
                raise TenantNotFoundError("API key not found or inactive")

            tenant_row = await conn.fetchrow(
                "SELECT * FROM tenants WHERE id = $1", key_record["tenant_id"]
            )
            if tenant_row is None:
                raise TenantNotFoundError(
                    f"tenant {key_record['tenant_id']} not found (orphan api_key)"
                )

            # last_used_at non-critical — hata log only, auth'u durdurmaz
            await tenant_api_key_repo.touch_last_used(conn, key_record["id"])

        tenant = self._row_to_tenant(tenant_row)
        if tenant.status == TenantStatus.SUSPENDED:
            raise TenantSuspendedError(f"tenant {tenant.slug} suspended")
        return tenant

    async def resolve_by_id(self, tenant_id: UUID) -> Tenant:
        """Direkt tenant ID lookup."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM tenants WHERE id = $1", tenant_id)
        if row is None:
            raise TenantNotFoundError(f"tenant_id {tenant_id} not found")

        tenant = self._row_to_tenant(row)
        if tenant.status == TenantStatus.SUSPENDED:
            raise TenantSuspendedError(f"tenant {tenant.slug} is suspended")
        return tenant

    async def resolve_by_source_ref(
        self,
        source_type: TenantSourceType,
        source_ref: str,
    ) -> Tenant | None:
        """Source_ref lookup — genelde 'standalone' veya 'partner_sub' için."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tenants WHERE source_type = $1 AND source_ref = $2",
                source_type.value,
                source_ref,
            )
        return self._row_to_tenant(row) if row else None

    async def get_config(self, tenant: Tenant) -> dict[str, Any]:
        """Standalone tenant config qualifier DB'de. Tenant entity'deki config
        fresh (resolve sırasında DB'den gelmiş)."""
        return dict(tenant.config)

    async def record_usage(self, tenant: Tenant, metric: str, value: int = 1) -> None:
        """Phase 1.E'de Redis counter + günlük aggregate pipeline eklenecek."""
        log.debug("tenant_usage_noop", tenant_id=str(tenant.id), metric=metric, value=value)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_tenant(row: Any) -> Tenant:
        """asyncpg.Record → Tenant dataclass (LisentCRMTenantAdapter ile aynı şema).

        asyncpg jsonb kolonları string olarak döner; json.loads ile parse ediyoruz
        (lead_repo.py aynı pattern'i kullanıyor).
        """
        return Tenant(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            source_type=TenantSourceType(row["source_type"]),
            source_ref=row["source_ref"],
            plan=TenantPlan(row["plan"]),
            status=TenantStatus(row["status"]),
            config=_parse_jsonb(row["config"]),
            domain_claims=list(row["domain_claims"] or []),
            outbound_webhook_url=row["outbound_webhook_url"],
            outbound_webhook_secret=row["outbound_webhook_secret"],
            branding=_parse_jsonb(row["branding"]),
            qualification_framework=QualificationFramework(row["qualification_framework"]),
            partner_id=row["partner_id"],
        )


def _parse_jsonb(value: Any) -> dict[str, Any]:
    """asyncpg jsonb → dict. None | '' | '{}' | string JSON | dict hepsini handle eder."""
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
