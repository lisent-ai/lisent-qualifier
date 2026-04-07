"""
GreenAPI sendMessage HTTP istemcisi.

Rate limit: 50 req/s per instance.
429 durumunda tenacity ile exponential backoff (1s → 8s, 3 deneme).
"""
import structlog
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.config import get_settings

log = structlog.get_logger(__name__)

_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        settings = get_settings()
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.greenapi_reply_timeout),
        )
    return _HTTP_CLIENT


async def close_greenapi_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT and not _HTTP_CLIENT.is_closed:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


class RateLimitError(Exception):
    """GreenAPI 429 Too Many Requests"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(RateLimitError),
    reraise=True,
)
async def send_whatsapp_message(
    id_instance: int,
    api_token: str,
    chat_id: str,
    message: str,
) -> bool:
    """
    GreenAPI sendMessage endpoint'ini çağırır.
    Başarıda True, kalıcı hata durumunda False döner.
    """
    settings = get_settings()
    url = (
        f"{settings.greenapi_base_url.rstrip('/')}"
        f"/waInstance{id_instance}/sendMessage/{api_token}"
    )
    client = _get_client()
    try:
        response = await client.post(url, json={"chatId": chat_id, "message": message})
        if response.status_code == 429:
            log.warning("greenapi_rate_limited", id_instance=id_instance)
            raise RateLimitError("GreenAPI rate limit exceeded")
        response.raise_for_status()
        log.info("whatsapp_message_sent", chat_id=chat_id, instance=id_instance)
        return True
    except RateLimitError:
        raise
    except Exception as exc:
        log.error("greenapi_send_failed", error=str(exc), chat_id=chat_id)
        return False
