"""
Webhook payload → FieldMappingResult.

İki katmanlı:
  1. heuristic_map  — alias tablosu ile hızlı, deterministik eşleştirme
  2. llm_map        — local LLM structured output (fallback)

Orchestrator: map_fields() — heuristic önce, name+phone bulamazsa LLM dener.
"""
import json
import re
import structlog
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.infrastructure.llm.schemas import FieldMappingResult

log = structlog.get_logger(__name__)

_mapping_adapter = TypeAdapter(FieldMappingResult)

# ── Alias tablosu (TR + EN, lowercase) ──────────────────────────────────────

_FIELD_ALIASES: dict[str, list[str]] = {
    "full_name": [
        "name", "full_name", "fullname", "ad", "isim", "ad_soyad", "adsoyad",
        "ad soyad", "musteri_adi", "müşteri_adı", "customer_name",
        "contact_name", "kullanici_adi", "adı",
        "ad_soyad_unvan", "lead_name",
    ],
    "phone": [
        "phone", "telefon", "tel", "gsm", "cep", "phone_number", "mobile",
        "cep_telefon", "cep_tel", "cep_no", "telefon_no", "telephone",
        "mobile_phone", "contact_phone", "whatsapp", "iletisim_no",
    ],
    "email": [
        "email", "e-posta", "eposta", "mail", "e_posta", "email_address",
        "e_mail", "contact_email",
    ],
    "city": [
        "city", "sehir", "şehir", "il", "location", "konum", "ilce", "ilçe",
        "adres", "address", "bolge", "bölge", "region",
    ],
    "source": [
        "source", "kaynak", "kanal", "channel", "utm_source", "referrer",
        "platform", "medium", "utm_medium",
    ],
    "project_type": [
        "project_type", "proje_tipi", "proje_turu", "proje_türü",
        "tip", "tur", "tür", "kategori", "category", "property_type",
        "project_interest.type", "project.type",
    ],
    "budget_range": [
        "budget_range", "butce", "bütçe", "budget", "fiyat_araligi",
        "price_range", "butce_araligi", "budget_range", "budget.range",
    ],
    "budget_amount": [
        "budget_amount", "butce_miktari", "butce_tutar", "amount", "tutar",
        "fiyat", "price", "miktar", "exact_amount", "budget.exact_amount",
        "budget.amount",
    ],
    "decision_authority": [
        "decision_authority", "karar_verici", "authority", "yetki", "rol",
        "role", "decision_maker", "decision.authority",
    ],
    "timeline_urgency": [
        "timeline_urgency", "timeline", "zaman", "aciliyet", "urgency",
        "ne_zaman", "when", "süre", "sure", "zamanlama",
        "timeline.urgency",
    ],
    "notes": [
        "notes", "not", "notlar", "aciklama", "açıklama", "description",
        "message", "mesaj", "detay", "detail", "yorum", "comment",
    ],
    "external_id": [
        "id", "form_id", "external_id", "submission_id", "entry_id",
        "kayit_id", "kayıt_id", "ref", "reference", "referans",
        "basvuru_no", "başvuru_no", "record_id",
    ],
}

# Reverse lookup: normalized alias → target field name
_ALIAS_MAP: dict[str, str] = {}
for _target, _aliases in _FIELD_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_MAP[_alias.lower().replace("-", "_").replace(" ", "_")] = _target


def _normalize_key(key: str) -> str:
    """Normalize a key for alias matching."""
    return key.lower().strip().replace("-", "_").replace(" ", "_")


def _flatten_one_level(payload: dict[str, Any]) -> dict[str, Any]:
    """Nested dict'leri bir seviye flatten et.

    {"contact": {"name": "Ali", "phone": "555"}} →
    {"contact.name": "Ali", "contact.phone": "555", "name": "Ali", "phone": "555"}

    Üst-düzey key'ler korunur, sadece dict value'lar açılır.
    Çakışan nested key'lerde ilk bulunan değer korunur (overwrite yapılmaz).
    """
    flat: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, dict):
            for nested_k, nested_v in v.items():
                # Dotted key her zaman ekle
                flat[f"{k}.{nested_k}"] = nested_v
                # Kısa key: sadece ilk bulunan kazanır (çakışma koruması)
                if nested_k not in flat and nested_k not in payload:
                    flat[nested_k] = nested_v
        else:
            flat[k] = v
    return flat


def _try_parse_int(value: Any) -> int | None:
    """Güvenli int parse. '1500000' → 1500000, 'bilinmiyor' → None."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        # Remove common separators: 1.500.000 → 1500000
        cleaned = value.replace(".", "").replace(",", "").replace(" ", "").strip()
        if cleaned.isdigit():
            return int(cleaned)
    return None


# ── Heuristic Mapper ────────────────────────────────────────────────────────

def heuristic_map(payload: dict[str, Any]) -> FieldMappingResult:
    """Alias tablosu ile deterministik field mapping."""
    flat = _flatten_one_level(payload)

    mapped: dict[str, Any] = {}
    used_keys: set[str] = set()

    # İlk pas: dotted key'leri de dene (ör. "budget.range" → budget_range)
    for raw_key, value in flat.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue

        norm = _normalize_key(raw_key)
        target = _ALIAS_MAP.get(norm)

        # Dotted key → compound alias dene: "budget.range" → "budget_range"
        if not target and "." in norm:
            compound = norm.replace(".", "_")
            target = _ALIAS_MAP.get(compound)

        if target and target not in mapped:
            if target == "budget_amount":
                parsed = _try_parse_int(value)
                if parsed is not None:
                    mapped[target] = parsed
                    used_keys.add(raw_key)
            else:
                mapped[target] = str(value).strip()
                used_keys.add(raw_key)

    # first_name + last_name → full_name birleştirme
    if "full_name" not in mapped:
        fn = flat.get("first_name") or flat.get("firstname") or flat.get("ad") or ""
        ln = flat.get("last_name") or flat.get("lastname") or flat.get("soyad") or flat.get("soyadi") or ""
        if fn or ln:
            combined = f"{fn} {ln}".strip()
            if combined:
                mapped["full_name"] = combined
                for k in ("first_name", "firstname", "ad", "last_name", "lastname", "soyad", "soyadi"):
                    if k in flat:
                        used_keys.add(k)

    # Kalan alanları extra_fields'e koy (orijinal payload key'leri ile)
    extra: dict[str, Any] = {}
    for k, v in payload.items():
        if k not in used_keys and v is not None:
            extra[k] = v

    # Nested key'lerin parent'larını da used_keys'den çıkar
    for uk in list(used_keys):
        if "." in uk:
            parent = uk.split(".")[0]
            extra.pop(parent, None)

    mapped["extra_fields"] = extra
    return FieldMappingResult(**mapped)


# ── LLM Mapper ──────────────────────────────────────────────────────────────

_FIELD_MAPPING_PROMPT = """\
Aşağıdaki JSON webhook payload'ını analiz et. Her alanın hangi hedefe karşılık geldiğini belirle ve değerlerini extract et.

Hedef alanlar:
- full_name: Kişinin tam adı
- phone: Telefon numarası
- email: E-posta adresi
- city: Şehir/lokasyon
- source: Lead kaynağı (instagram, facebook, website, whatsapp, vb.)
- project_type: Proje tipi (residential, commercial, industrial, renovation, land)
- budget_range: Bütçe aralığı (under_500k, 500k_1m, 1m_3m, 3m_10m, over_10m)
- budget_amount: Sayısal bütçe miktarı (TL, sadece integer)
- decision_authority: Karar verici (sole, joint, influencer)
- timeline_urgency: Zaman çerçevesi (immediate, short, medium, long)
- notes: Ek notlar/açıklama
- external_id: Formun kendi ID'si

Eşleşmeyen tüm alanları extra_fields dict'ine koy.

Payload:
```json
{json_payload}
```

JSON olarak yanıt ver."""


def _build_field_mapping_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "field_mapping",
            "strict": True,
            "schema": FieldMappingResult.model_json_schema(),
        },
    }


async def llm_map(payload: dict[str, Any]) -> FieldMappingResult:
    """Local LLM ile field mapping (structured output)."""
    from app.infrastructure.llm.local_llm_client import _call_local_llm, _extract_json

    json_str = json.dumps(payload, ensure_ascii=False, indent=2)
    prompt = _FIELD_MAPPING_PROMPT.format(json_payload=json_str)
    messages = [{"role": "user", "content": prompt}]

    try:
        raw = await _call_local_llm(
            messages,
            timeout=30.0,
            max_tokens=1024,
            response_format=_build_field_mapping_response_format(),
        )
        return _mapping_adapter.validate_json(raw)
    except (ValidationError, Exception):
        # Fallback: response_format desteklenmiyorsa regex ile dene
        try:
            raw = await _call_local_llm(messages, timeout=30.0, max_tokens=1024)
            json_text = _extract_json(raw)
            return _mapping_adapter.validate_json(json_text)
        except Exception as exc:
            log.warning("llm_field_mapping_failed", error=str(exc))
            raise


# ── Orchestrator ─────────────────────────────────────────────────────────────

async def map_fields(payload: dict[str, Any]) -> FieldMappingResult:
    """Webhook payload'ını field'lara eşleştir.

    1. Heuristic mapping dene
    2. full_name + phone bulunduysa → heuristic sonucu dön (LLM'e gerek yok)
    3. Bulunamadıysa → LLM dene
    4. LLM fail olursa → heuristic sonucuna fallback
    """
    heuristic_result = heuristic_map(payload)

    # Heuristic yeterli mi? (name + phone bulundu)
    if heuristic_result.full_name and heuristic_result.phone:
        log.info("field_mapping_heuristic", name=bool(heuristic_result.full_name),
                 phone=bool(heuristic_result.phone))
        return heuristic_result

    # LLM'e danış
    try:
        llm_result = await llm_map(payload)
        log.info("field_mapping_llm", name=bool(llm_result.full_name),
                 phone=bool(llm_result.phone))
        return llm_result
    except Exception as exc:
        log.warning("field_mapping_llm_fallback", error=str(exc))
        return heuristic_result
