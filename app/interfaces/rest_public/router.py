"""
v1 router — tüm v1 endpoint'lerinin ana router'ı.

`app.main.create_app()` bu router'ı mount eder. Mevcut legacy routes (webhook,
chat, whatsapp, leads, sessions, rag) ayrı kalır — paralel layer.
"""

from fastapi import APIRouter

from app.interfaces.rest_public.v1 import api_keys as v1_api_keys
from app.interfaces.rest_public.v1 import config as v1_config
from app.interfaces.rest_public.v1 import credentials as v1_credentials
from app.interfaces.rest_public.v1 import health as v1_health
from app.interfaces.rest_public.v1 import internal_events as v1_internal_events
from app.interfaces.rest_public.v1 import leads as v1_leads
from app.interfaces.rest_public.v1 import scores as v1_scores
from app.interfaces.rest_public.v1 import tenant as v1_tenant
from app.interfaces.rest_public.v1 import usage as v1_usage
from app.interfaces.rest_public.v1 import webhook_config as v1_webhook_config

v1_router = APIRouter(prefix="/v1", tags=["v1"])

v1_router.include_router(v1_health.router)
v1_router.include_router(v1_tenant.router)
v1_router.include_router(v1_api_keys.router)
v1_router.include_router(v1_leads.router)
v1_router.include_router(v1_scores.router)
v1_router.include_router(v1_config.router)
v1_router.include_router(v1_webhook_config.router)
v1_router.include_router(v1_usage.router)
v1_router.include_router(v1_credentials.router)
v1_router.include_router(v1_internal_events.router)
