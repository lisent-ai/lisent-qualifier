"""
LisentCRMTenantAdapter — Mevcut Lisent CRM entegrasyonunu TenantPort'a uydurur.

Bu adapter, mevcut `app.infrastructure.crm.rest_client` modülündeki fonksiyonları
çağırarak Tenant resolution ve config fetch yapar. Kod duplikasyonu yok — wrapper
pattern.

Tenant mapping:
    - Webhook token → CRM company_id → `Tenant(source_type=LISENT_CRM, source_ref=<company_id>)`
    - `Tenant.id`: qualifier DB'deki `tenants.id` (backfill migration bunu yarattı)
    - `Tenant.config`: CRM'in `ai_config` JSONB'si (her çağrıda fresh fetch, cache Phase 1.E'de)

Şu anda (Phase 1.D.1):
    - `resolve_by_webhook_token` CRM lookup + qualifier DB tenants tablosundan ID eşlemesi
    - `resolve_by_id`, `resolve_by_source_ref` qualifier DB lookup
    - `resolve_by_api_key` NotImplementedError (bu adapter CRM-based, API key'i yönetmez)
    - `get_config` CRM fetch
    - `record_usage` no-op log (Phase 1.E'de Redis counter)

Phase 1.E:
    - Tenant cache eklenecek (5 dk TTL, tenants tablosu + config)
    - `record_usage` Redis'e bağlanacak
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from app.infrastructure.crm import rest_client as crm_rest_client
from app.ports.tenant import (
    QualificationFramework,
    Tenant,
    TenantNotFoundError,
    TenantPlan,
    TenantPort,
    TenantSourceType,
    TenantStatus,
)

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger(__name__)


class LisentCRMTenantAdapter(TenantPort):
    """TenantPort over Lisent CRM REST API + qualifier's tenants table."""

    def __init__(self, db_pool: asyncpg.Pool) -> None:
        self._pool = db_pool

    async def resolve_by_webhook_token(self, token: str) -> Tenant:
        """Webhook token → CRM company lookup → qualifier tenants row.

        Flow:
            1. CRM'den token → company_id al (mevcut `lookup_company_by_qualifier_token`)
            2. Qualifier DB'de `tenants` satırını source_ref=company_id ile bul
            3. Yoksa auto-provision (migration backfill kaçırmış olabilir)
            4. Tenant entity döndür
        """
        result = await crm_rest_client.lookup_company_by_qualifier_token(token)
        if result is None or not result.get("company_id"):
            raise TenantNotFoundError(f"No CRM company for qualifier token")

        company_id = str(result["company_id"])
        fallback_url = result.get("fallback_url")

        tenant = await self._find_or_create_legacy_tenant(company_id, fallback_url)
        return tenant

    async def resolve_by_api_key(self, api_key: str) -> Tenant:
        """LisentCRMTenantAdapter API key yönetmez — standalone adapter'a delege."""
        raise NotImplementedError(
            "LisentCRMTenantAdapter does not resolve by API key. "
            "Use StandaloneTenantAdapter for API key auth."
        )

    async def resolve_by_id(self, tenant_id: UUID) -> Tenant:
        """Tenant ID ile direkt qualifier DB lookup."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tenants WHERE id = $1 AND source_type = 'lisent_crm'",
                tenant_id,
            )
        if row is None:
            raise TenantNotFoundError(f"tenant_id {tenant_id} not found in lisent_crm source")
        return self._row_to_tenant(row)

    async def resolve_by_source_ref(
        self,
        source_type: TenantSourceType,
        source_ref: str,
    ) -> Tenant | None:
        """CRM company_id → tenant lookup."""
        if source_type != TenantSourceType.LISENT_CRM:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tenants WHERE source_type = $1 AND source_ref = $2",
                source_type.value,
                source_ref,
            )
        return self._row_to_tenant(row) if row else None

    async def get_config(self, tenant: Tenant) -> dict[str, Any]:
        """CRM'den ai_config fetch eder.

        NOT: Phase 1.E'de in-memory / Redis cache eklenecek (5 dk TTL).
        Şu anda her çağrıda CRM'e gider — mevcut davranış korunuyor.
        """
        if tenant.source_type != TenantSourceType.LISENT_CRM or tenant.source_ref is None:
            return dict(tenant.config)  # local config'e düş

        crm_config = await crm_rest_client.fetch_company_ai_config(tenant.source_ref)
        return crm_config or dict(tenant.config)

    async def record_usage(self, tenant: Tenant, metric: str, value: int = 1) -> None:
        """Phase 1.E'de Redis counter'ına yazacak. Şimdilik log only."""
        log.debug("tenant_usage_noop", tenant_id=str(tenant.id), metric=metric, value=value)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _find_or_create_legacy_tenant(
        self,
        company_id: str,
        fallback_url: str | None = None,
    ) -> Tenant:
        """
        Qualifier DB'de `tenants` satırını bul veya yarat (legacy source_type).

        Migration backfill'i zaten çoğu mevcut company için yaratmış olmalı;
        bu fonksiyon yeni eklenen CRM company'ler için auto-provisioning sağlar.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tenants WHERE source_type='lisent_crm' AND source_ref=$1",
                company_id,
            )
            if row:
                return self._row_to_tenant(row)

            # Lazy backfill — yeni company için tenant oluştur
            log.info("lisent_crm_tenant_autocreate", company_id=company_id)
            row = await conn.fetchrow(
                """
                INSERT INTO tenants (slug, name, source_type, source_ref, plan, status, outbound_webhook_url, config)
                VALUES ($1, $2, 'lisent_crm', $3, 'legacy', 'active', $4, '{}'::jsonb)
                ON CONFLICT (slug) DO UPDATE SET updated_at = now()
                RETURNING *
                """,
                f"legacy-{company_id.replace('-', '')}",
                f"Legacy Company {company_id[:8]}",
                company_id,
                fallback_url,
            )
            return self._row_to_tenant(row)

    @staticmethod
    def _row_to_tenant(row: Any) -> Tenant:
        """asyncpg.Record → Tenant dataclass."""
        return Tenant(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            source_type=TenantSourceType(row["source_type"]),
            source_ref=row["source_ref"],
            plan=TenantPlan(row["plan"]),
            status=TenantStatus(row["status"]),
            config=dict(row["config"]) if row["config"] else {},
            domain_claims=list(row["domain_claims"] or []),
            outbound_webhook_url=row["outbound_webhook_url"],
            outbound_webhook_secret=row["outbound_webhook_secret"],
            branding=dict(row["branding"]) if row["branding"] else {},
            qualification_framework=QualificationFramework(row["qualification_framework"]),
            partner_id=row["partner_id"],
        )
