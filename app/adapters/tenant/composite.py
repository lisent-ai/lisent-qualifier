"""
CompositeTenantAdapter — Standalone + LisentCRM chain.

Resolve sırasında önce standalone tenants tablosunu dener (yeni external
müşteriler); yoksa legacy CRM lookup'a düşer (backward-compat).

Kullanım:
    adapter = CompositeTenantAdapter(
        primary=StandaloneTenantAdapter(pool),
        fallback=LisentCRMTenantAdapter(pool),
    )
    tenant = await adapter.resolve_by_webhook_token("sk_test_slug")
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from app.ports.tenant import (
    Tenant,
    TenantNotFoundError,
    TenantPort,
    TenantSourceType,
)

log = structlog.get_logger(__name__)


class CompositeTenantAdapter(TenantPort):
    """Chain of responsibility: primary first, fallback on TenantNotFoundError."""

    def __init__(self, primary: TenantPort, fallback: TenantPort) -> None:
        self._primary = primary
        self._fallback = fallback

    async def resolve_by_webhook_token(self, token: str) -> Tenant:
        try:
            return await self._primary.resolve_by_webhook_token(token)
        except (TenantNotFoundError, NotImplementedError):
            return await self._fallback.resolve_by_webhook_token(token)

    async def resolve_by_api_key(self, api_key: str) -> Tenant:
        try:
            return await self._primary.resolve_by_api_key(api_key)
        except (TenantNotFoundError, NotImplementedError):
            return await self._fallback.resolve_by_api_key(api_key)

    async def resolve_by_id(self, tenant_id: UUID) -> Tenant:
        try:
            return await self._primary.resolve_by_id(tenant_id)
        except (TenantNotFoundError, NotImplementedError):
            return await self._fallback.resolve_by_id(tenant_id)

    async def resolve_by_source_ref(
        self,
        source_type: TenantSourceType,
        source_ref: str,
    ) -> Tenant | None:
        result = await self._primary.resolve_by_source_ref(source_type, source_ref)
        if result is not None:
            return result
        return await self._fallback.resolve_by_source_ref(source_type, source_ref)

    async def get_config(self, tenant: Tenant) -> dict[str, Any]:
        # Config source tenant'ın source_type'ına göre routing
        if tenant.source_type == TenantSourceType.LISENT_CRM:
            return await self._fallback.get_config(tenant)
        return await self._primary.get_config(tenant)

    async def record_usage(self, tenant: Tenant, metric: str, value: int = 1) -> None:
        # İkisine de raporla (birinde no-op ise zarar yok)
        await self._primary.record_usage(tenant, metric, value)
        # Fallback opsiyonel — skip etme, aynı tenant iki kere sayılmasın
