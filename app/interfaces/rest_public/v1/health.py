"""
GET /v1/health — public (auth gerektirmez) health check.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["v1-health"])
async def v1_health() -> dict:
    """Basit liveness probe.

    Auth yok — load balancer + uptime monitors için.
    """
    return {"status": "ok", "api_version": "v1"}
