"""
GET /v1/tenant/me — current tenant info (from API key).

Bu endpoint canary — tam wiring zincirini kanıtlar:
    1. FastAPI request → `get_current_tenant` (auth.py)
    2. HTTP Bearer extract → `get_tenant_adapter` (di.py) → `LisentCRMTenantAdapter`
    3. `resolve_by_webhook_token()` → qualifier DB'de tenants row'u
    4. Tenant dataclass → response

Başarılı cevap = ports/adapters katmanı baştan sona çalışıyor.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.interfaces.rest_public.auth import get_current_tenant
from app.ports.tenant import Tenant

router = APIRouter(prefix="/tenant", tags=["v1-tenant"])


@router.get("/me")
async def get_my_tenant(tenant: Annotated[Tenant, Depends(get_current_tenant)]) -> dict:
    """Current tenant (API key'den resolve edildi).

    Response şekli (MVP; Phase 2'de usage metrikleri ve plan limits eklenecek):
        {
            "id": "<uuid>",
            "slug": "...",
            "name": "...",
            "source_type": "lisent_crm" | "standalone" | "partner_sub",
            "plan": "...",
            "status": "...",
            "qualification_framework": "champ"
        }
    """
    return {
        "id": str(tenant.id),
        "slug": tenant.slug,
        "name": tenant.name,
        "source_type": tenant.source_type.value,
        "plan": tenant.plan.value,
        "status": tenant.status.value,
        "qualification_framework": tenant.qualification_framework.value,
        "domain_claims": tenant.domain_claims,
        "is_legacy_crm": tenant.is_legacy_crm,
    }
