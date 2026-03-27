import structlog
from fastapi import APIRouter

from app.infrastructure.redis.client import get_redis
from app.infrastructure.llm.local_llm_client import get_http_client

log = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict:
    checks: dict[str, str] = {}

    # Redis check
    try:
        redis = get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        log.error("readiness_redis_fail", error=str(exc))
        checks["redis"] = f"error: {exc}"

    # Local LLM check (optional — don't fail readiness if LLM is cold)
    try:
        client = get_http_client()
        response = await client.get("/health", timeout=3.0)
        checks["local_llm"] = "ok" if response.is_success else f"http_{response.status_code}"
    except Exception as exc:
        checks["local_llm"] = f"unavailable: {exc}"

    all_critical_ok = checks.get("redis") == "ok"
    return {
        "status": "ready" if all_critical_ok else "degraded",
        "checks": checks,
    }
