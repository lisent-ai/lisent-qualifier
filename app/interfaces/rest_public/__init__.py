"""
Public REST API (v1) — external tenants için.

Scope (Phase 1.E):
    - Tenant context middleware (API key / JWT → Tenant resolve)
    - DI factory (port → concrete adapter)
    - Minimal v1 endpoints: GET /v1/health, GET /v1/tenant/me

Phase 2'de genişleyecek:
    - POST /v1/leads, GET /v1/leads/{id}, GET /v1/leads/{id}/score/stream (SSE)
    - OAuth2 flow /oauth/authorize, /oauth/token, /oauth/revoke
    - Widget iframe config endpoint
    - Webhook CRUD
"""

from app.interfaces.rest_public.router import v1_router

__all__ = ["v1_router"]
