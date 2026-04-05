"""
CRM webhook client with retry, circuit breaker, and Redis dead-letter outbox.
"""
import json
import structlog
from typing import Any

import httpx
from circuitbreaker import circuit
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import get_settings

log = structlog.get_logger(__name__)

_HTTP_CLIENT: httpx.AsyncClient | None = None


def get_crm_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        settings = get_settings()
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.crm_webhook_timeout),
            http2=True,
        )
    return _HTTP_CLIENT


async def close_crm_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT and not _HTTP_CLIENT.is_closed:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


@circuit(failure_threshold=3, recovery_timeout=120, expected_exception=Exception)
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
async def _post_to_url(url: str, payload: dict[str, Any], token: str = "") -> None:
    client = get_crm_client()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = await client.post(url, json=payload, headers=headers)
    response.raise_for_status()
    log.info("crm_webhook_sent", status=response.status_code, url=url)


async def send_to_crm(
    payload: dict[str, Any],
    session_repo=None,
    fallback_url: str | None = None,
) -> bool:
    """
    Qualified lead'i webhook URL'e gönderir.
    Önce şirkete özel fallback_url denenir, yoksa global CRM_WEBHOOK_URL kullanılır.
    İkisi de yoksa sessizce atlanır.
    """
    settings = get_settings()
    target_url = fallback_url or settings.crm_webhook_url
    if not target_url:
        log.warning("crm_webhook_skipped", reason="no webhook URL configured", score=payload.get("score"))
        return False

    try:
        await _post_to_url(target_url, payload, settings.crm_webhook_token)
        return True
    except Exception as exc:
        log.error("crm_webhook_failed", error=str(exc))
        if session_repo is not None:
            envelope = {
                "webhook_url": target_url,
                "token": settings.crm_webhook_token,
                "payload": payload,
            }
            await session_repo.push_crm_outbox(envelope)
            log.info("crm_outbox_queued")
        return False


async def flush_outbox(session_repo, max_items: int = 50) -> int:
    """Flush pending CRM outbox items. Returns number of successfully sent items."""
    settings = get_settings()
    sent = 0
    for _ in range(max_items):
        item = await session_repo.pop_crm_outbox()
        if item is None:
            break
        try:
            # Support both envelope format (with url/token) and legacy plain payload
            if "webhook_url" in item and "payload" in item:
                url = item["webhook_url"]
                token = item.get("token", settings.crm_webhook_token)
                payload = item["payload"]
            else:
                url = settings.crm_webhook_url
                token = settings.crm_webhook_token
                payload = item
                if not url:
                    log.warning("crm_outbox_no_url", item_keys=list(item.keys()))
                    continue
            await _post_to_url(url, payload, token)
            sent += 1
        except Exception as exc:
            log.error("crm_outbox_flush_failed", error=str(exc))
            await session_repo.push_crm_outbox(item)
            break
    return sent
