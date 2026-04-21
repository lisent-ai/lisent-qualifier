"""
TenantPort adapters.

Her adapter `app.ports.TenantPort`'u implement eder ve farklı bir tenant
kaynağıyla konuşur:
    - LisentCRMTenantAdapter — Lisent CRM REST API'si (legacy company lookup)
    - StandaloneTenantAdapter — qualifier'ın kendi `tenants` tablosu (yeni müşteriler)
    - CompositeTenantAdapter (Phase 1.E) — ikisinin merge'i (önce standalone, yoksa CRM)
"""

from app.adapters.tenant.lisent_crm import LisentCRMTenantAdapter
from app.adapters.tenant.standalone import StandaloneTenantAdapter

__all__ = ["LisentCRMTenantAdapter", "StandaloneTenantAdapter"]
