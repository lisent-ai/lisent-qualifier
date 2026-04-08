"""
CRM REST API istemcisi — WhatsApp kanalı için customer/lead oluşturma
ve GreenAPI instance lookup işlemleri.

Mevcut webhook_client.py'den bağımsız; farklı endpoint grubu kullanır.
"""
import structlog
import httpx
from typing import Optional

from app.config import get_settings

log = structlog.get_logger(__name__)

_CRM_CLIENT: httpx.AsyncClient | None = None


def _get_crm_rest_client() -> httpx.AsyncClient:
    global _CRM_CLIENT
    if _CRM_CLIENT is None or _CRM_CLIENT.is_closed:
        settings = get_settings()
        if not settings.crm_base_url:
            raise RuntimeError("CRM_BASE_URL is not configured")
        _CRM_CLIENT = httpx.AsyncClient(
            base_url=settings.crm_base_url.rstrip("/"),
            headers={"X-API-KEY": settings.crm_api_key},
            timeout=httpx.Timeout(10.0),
        )
    return _CRM_CLIENT


async def close_crm_rest_client() -> None:
    global _CRM_CLIENT
    if _CRM_CLIENT and not _CRM_CLIENT.is_closed:
        await _CRM_CLIENT.aclose()
        _CRM_CLIENT = None


async def lookup_company_by_qualifier_token(token: str) -> Optional[dict]:
    """
    AI Lead Qualifier webhook token'ına göre company_id ve fallback_url döner.
    Geçersiz token için None döner.
    Başarılı: {"company_id": "...", "fallback_url": "..." | None}
    """
    try:
        client = _get_crm_rest_client()
        resp = await client.get(f"/internal/company/lookup-by-qualifier-token/{token}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return None
    except Exception as exc:
        log.error("crm_qualifier_token_lookup_failed", error=str(exc))
        return None


async def fetch_company_ai_config(company_id: str) -> Optional[dict]:
    """
    CRM'den company_id'ye göre AI agent config döner.
    Config yoksa None döner (CRM auto-create yapacak).
    """
    try:
        client = _get_crm_rest_client()
        resp = await client.get(f"/internal/company/{company_id}/ai-config")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return None
    except Exception as exc:
        log.error("crm_ai_config_fetch_failed", error=str(exc), company_id=company_id)
        return None


async def fetch_company_kb_documents(company_id: str) -> list[dict] | None:
    """
    CRM'den company_id'ye göre bilgi bankası dokümanlarını döner.
    Doküman yoksa boş liste döner.
    """
    try:
        client = _get_crm_rest_client()
        resp = await client.get(
            f"/internal/company/{company_id}/kb-documents",
            params={"include_content": "true"},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return []
    except Exception as exc:
        log.error("crm_kb_docs_fetch_failed", error=str(exc), company_id=company_id)
        return []


async def lookup_company_by_rag_token(token: str) -> Optional[dict]:
    """
    RAG webhook token'ına göre company_id döner.
    Geçersiz token için None döner.
    Başarılı: {"company_id": "..."}
    """
    try:
        client = _get_crm_rest_client()
        resp = await client.get(f"/internal/company/lookup-by-rag-token/{token}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return None
    except Exception as exc:
        log.error("crm_rag_token_lookup_failed", error=str(exc))
        return None


async def store_webhook_data(company_id: str, payload: object) -> Optional[dict]:
    """
    Gelen RAG webhook verisini CRM'e kaydet.
    """
    try:
        client = _get_crm_rest_client()
        resp = await client.post(
            f"/internal/company/{company_id}/webhook-data",
            json={"payload": payload, "label": ""},
        )
        resp.raise_for_status()
        return resp.json()
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return None
    except Exception as exc:
        log.error("crm_webhook_data_store_failed", error=str(exc), company_id=company_id)
        return None


async def fetch_company_webhook_data(company_id: str) -> list[dict] | None:
    """
    CRM'den company_id'ye göre webhook verilerini döner (prompt enjeksiyonu için).
    """
    try:
        client = _get_crm_rest_client()
        resp = await client.get(
            f"/internal/company/{company_id}/webhook-data",
            params={"include_content": "true"},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return []
    except Exception as exc:
        log.error("crm_webhook_data_fetch_failed", error=str(exc), company_id=company_id)
        return []


async def fetch_company_fallback_url(company_id: str) -> Optional[str]:
    """
    CRM'den company_id'ye göre qualifier fallback_url döner.
    /internal/company/{id}/qualifier-config endpoint'ini kullanır.
    Bulamazsa None döner.
    """
    try:
        client = _get_crm_rest_client()
        resp = await client.get(f"/internal/company/{company_id}/qualifier-config")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data.get("fallback_url") or None
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return None
    except Exception as exc:
        log.warning("crm_fallback_url_fetch_failed", error=str(exc), company_id=company_id)
        return None


async def lookup_greenapi_by_company(company_id: str) -> Optional[dict]:
    """
    CRM'den company_id'ye göre GreenAPI credentials döner.
    Entegrasyon yoksa None döner.
    Response: {"id_instance": "...", "api_token_instance": "...", ...}
    """
    try:
        client = _get_crm_rest_client()
        resp = await client.get(f"/companies/{company_id}/integrations/greenapi")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        # Bu endpoint masked token döndürür — internal lookup gerekebilir
        # Ama id_instance'ı aldıktan sonra internal lookup yapabiliriz
        id_instance = data.get("id_instance")
        if not id_instance:
            return None
        # Plaintext token için internal lookup
        return await lookup_greenapi_integration(id_instance)
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return None
    except Exception as exc:
        log.error("crm_greenapi_company_lookup_failed", error=str(exc), company_id=company_id)
        return None


async def lookup_greenapi_integration(id_instance: str) -> Optional[dict]:
    """
    CRM'den idInstance'a göre şirket bilgisi ve plaintext API token'ı döner.
    Bilinmeyen instance için None döner.
    """
    try:
        client = _get_crm_rest_client()
        resp = await client.get(f"/internal/greenapi/lookup/{id_instance}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return None
    except Exception as exc:
        log.error("crm_greenapi_lookup_failed", error=str(exc), id_instance=id_instance)
        return None


async def find_or_create_customer(
    company_id: str,
    phone: str,
    name: str,
) -> Optional[dict]:
    """
    CRM'de telefon numarasına göre customer arar; bulamazsa oluşturur.
    Başarı durumunda customer dict'i döner, hata durumunda None.
    """
    try:
        client = _get_crm_rest_client()

        # Önce telefon ile ara
        resp = await client.get(
            "/customers",
            params={"company_id": company_id, "phone": phone, "limit": "1"},
        )
        resp.raise_for_status()
        data = resp.json()
        customers = data.get("data") or []
        if customers:
            return customers[0]

        # Bulunamazsa oluştur
        resp = await client.post(
            "/customers",
            json={
                "company_id": company_id,
                "name": name or phone,
                "phone": phone,
            },
        )
        resp.raise_for_status()
        return resp.json()
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return None
    except Exception as exc:
        log.error("crm_customer_upsert_failed", error=str(exc), phone=phone)
        return None


async def create_lead(
    company_id: str,
    customer_id: str,
    source: str = "whatsapp",
    extra_data: Optional[dict] = None,
) -> Optional[dict]:
    """
    CRM'de yeni lead oluşturur.
    Başarı durumunda lead dict'i döner, hata durumunda None.
    """
    try:
        client = _get_crm_rest_client()
        resp = await client.post(
            "/leads",
            json={
                "company_id": company_id,
                "customer_id": customer_id,
                "source": source,
                "status": "new",
                "extra_data": extra_data or {},
            },
        )
        resp.raise_for_status()
        return resp.json()
    except RuntimeError as exc:
        log.error("crm_rest_not_configured", error=str(exc))
        return None
    except Exception as exc:
        log.error("crm_lead_create_failed", error=str(exc))
        return None
