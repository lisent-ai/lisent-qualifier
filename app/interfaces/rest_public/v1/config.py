"""
Tenant config endpoints — GET/PATCH /v1/config.

Tenant kendi config'ini okur + değiştirir (qualification_framework, threshold,
scoring weights, handoff_aggressiveness vs).

Multi-framework (Phase 2.F): CHAMP/BANT/MEDDIC enum — tenant.qualification_framework
kolonu migration 003'te var, framework-specific prompt routing Phase 2'de
build edilecek. Şu an sadece enum + config exposure.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.infrastructure.db.pool import get_db_pool
from app.interfaces.rest_public.auth import get_current_tenant
from app.ports.tenant import QualificationFramework, Tenant

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/config", tags=["v1-config"])


class TenantConfigResponse(BaseModel):
    tenant_id: UUID
    qualification_framework: str
    supported_frameworks: list[str] = Field(
        default_factory=lambda: [f.value for f in QualificationFramework]
    )
    config: dict[str, Any]
    domain_claims: list[str]
    outbound_webhook_url: str | None
    branding: dict[str, Any]


class ConfigPatchRequest(BaseModel):
    qualification_framework: Literal["champ", "bant", "meddic"] | None = None
    qualification_threshold: int | None = Field(default=None, ge=0, le=100)
    handoff_aggressiveness: Literal["conservative", "balanced", "aggressive"] | None = None
    scoring_weights: dict[str, float] | None = None
    outbound_webhook_url: str | None = None
    branding: dict[str, Any] | None = None
    cta_calendly_url: str | None = None


@router.get("", response_model=TenantConfigResponse)
async def get_config(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> TenantConfigResponse:
    return TenantConfigResponse(
        tenant_id=tenant.id,
        qualification_framework=tenant.qualification_framework.value,
        config=dict(tenant.config),
        domain_claims=list(tenant.domain_claims),
        outbound_webhook_url=tenant.outbound_webhook_url,
        branding=dict(tenant.branding),
    )


@router.patch("", response_model=TenantConfigResponse)
async def patch_config(
    body: ConfigPatchRequest,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> TenantConfigResponse:
    """Tenant config'inin bir alt kümesini güncelle.

    framework → tenants.qualification_framework kolonu
    Diğerleri → tenants.config JSONB (scalar merge)
    """
    pool = get_db_pool()
    updates: list[str] = []
    params: list[Any] = [tenant.id]

    # Framework — direct column
    if body.qualification_framework is not None:
        params.append(body.qualification_framework)
        updates.append(f"qualification_framework = ${len(params)}")

    # outbound_webhook_url — direct column
    if body.outbound_webhook_url is not None:
        params.append(body.outbound_webhook_url)
        updates.append(f"outbound_webhook_url = ${len(params)}")

    # branding — JSONB merge
    if body.branding is not None:
        params.append(body.branding)
        updates.append(f"branding = branding || ${len(params)}::jsonb")

    # config JSONB merge — per field
    config_patch: dict[str, Any] = {}
    if body.qualification_threshold is not None:
        config_patch["qualification_threshold"] = body.qualification_threshold
    if body.handoff_aggressiveness is not None:
        config_patch["handoff_aggressiveness"] = body.handoff_aggressiveness
    if body.scoring_weights is not None:
        config_patch["scoring_weights"] = body.scoring_weights
    if body.cta_calendly_url is not None:
        config_patch["cta_calendly_url"] = body.cta_calendly_url

    if config_patch:
        import json

        params.append(json.dumps(config_patch))
        updates.append(f"config = config || ${len(params)}::jsonb")

    if not updates:
        # No-op — return current
        return await get_config(tenant=tenant)

    query = f"UPDATE tenants SET {', '.join(updates)}, updated_at = now() WHERE id = $1 RETURNING *"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *params)

    log.info(
        "tenant_config_updated",
        tenant_id=str(tenant.id),
        fields=list(body.model_dump(exclude_none=True).keys()),
    )

    # Re-build Tenant-style response from fresh row
    import json

    config_val = row["config"]
    if isinstance(config_val, str):
        config_val = json.loads(config_val) if config_val else {}
    branding_val = row["branding"]
    if isinstance(branding_val, str):
        branding_val = json.loads(branding_val) if branding_val else {}

    return TenantConfigResponse(
        tenant_id=row["id"],
        qualification_framework=row["qualification_framework"],
        config=config_val or {},
        domain_claims=list(row["domain_claims"] or []),
        outbound_webhook_url=row["outbound_webhook_url"],
        branding=branding_val or {},
    )
