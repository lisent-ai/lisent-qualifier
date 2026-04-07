"""
GreenAPI sendMessage HTTP istemcisi.

Rate limit: 50 req/s per instance.
429 durumunda tenacity ile exponential backoff (1s → 8s, 3 deneme).
"""
import math
import random
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


def compute_typing_delay(text: str) -> float:
    """
    İnsan benzeri yazma gecikmesi hesapla (saniye cinsinden).

    sqrt(karakter_sayısı) tabanlı formül kullanır:
    - Kısa mesajlar yavaş (düşünme + yazma)
    - Uzun mesajlar orantısız hızlı (flow state, otomatik tamamlama)

    Örnekler:
      10  char →  ~4.0s   ("Merhaba! 😊")
      40  char →  ~6.6s   ("Evet, villa projeleri 300K€'dan başlıyor.")
     100  char →  ~9.5s   (orta uzunlukta cevap)
     150  char → ~11.3s   (detaylı cevap)
     300  char → ~15.4s   (uzun paragraf)
     500+ char → ~18.0s   (maksimum — daha uzun bekleme doğal hissettirmez)
    """
    char_count = len(text)
    if char_count == 0:
        return 2.0

    # base: okuma + düşünme süresi
    base = 1.5
    # sqrt eğrisi: uzun mesajlarda hızlanma etkisi
    typing = math.sqrt(char_count) * 0.8
    total = base + typing

    # Doğal varyasyon: -%10 ile +%20 arası rastgele sapma
    jitter = random.uniform(-0.10, 0.20)
    total *= (1 + jitter)

    # 2s–18s arası sınırla (GreenAPI max 20s, 2s headroom bırak)
    return max(2.0, min(total, 18.0))


async def send_typing_presence(
    id_instance: int,
    api_token: str,
    chat_id: str,
    typing_time: int = 15000,
) -> bool:
    """
    GreenAPI sendTyping — karşı tarafa "yazıyor..." gösterir.
    typing_time: ms cinsinden süre (1000-20000). Varsayılan 15 saniye.
    Fire-and-forget: hata durumunda log yazar, akışı bloklamaz.
    """
    settings = get_settings()
    url = (
        f"{settings.greenapi_base_url.rstrip('/')}"
        f"/waInstance{id_instance}/sendTyping/{api_token}"
    )
    client = _get_client()
    try:
        response = await client.post(
            url, json={"chatId": chat_id, "typingTime": typing_time},
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        log.debug("greenapi_typing_failed", error=str(exc), chat_id=chat_id)
        return False
