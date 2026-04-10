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
        "data.phone_number", "data.phone", "data.tel",
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
        "medium", "utm_medium",
    ],
    "project_type": [
        "project_type", "proje_tipi", "proje_turu", "proje_türü",
        "tip", "tur", "tür", "kategori", "category", "property_type",
        "project_interest.type", "project.type",
        "what_type_of_property_are_you_interested_in?",
        "what_type_of_property_are_you_interested_in",
        "property_interest", "property_type_interest",
    ],
    "budget_range": [
        "budget_range", "butce", "bütçe", "budget", "fiyat_araligi",
        "price_range", "butce_araligi", "budget_range", "budget.range",
        "what_is_your_budget_range?", "what_is_your_budget_range",
        "budget_range?",
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
        "why_are_you_interested_in_north_cyprus?",
        "why_are_you_interested", "interest_reason", "reason",
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
    """Normalize a key for alias matching.

    Handles form-style question keys like 'what_is_your_budget_range?'
    """
    return key.lower().strip().replace("-", "_").replace(" ", "_")


def _flatten_one_level(payload: dict[str, Any]) -> dict[str, Any]:
    """Nested dict'leri bir seviye flatten et.

    {"contact": {"name": "Ali", "phone": "555"}} →
    {"contact.name": "Ali", "contact.phone": "555", "name": "Ali", "phone": "555"}

    Özel durum: "data" key'i varsa ve dict ise, içeriği doğrudan üst seviyeye taşınır.
    Bu, CRM'den gelen {"data": {...lead fields...}, "event": "...", ...} yapısını destekler.

    Üst-düzey key'ler korunur, sadece dict value'lar açılır.
    Çakışan nested key'lerde ilk bulunan değer korunur (overwrite yapılmaz).
    """
    flat: dict[str, Any] = {}

    # "data" key'i varsa ve dict ise, önce onun içeriğini üst seviyeye taşı
    data_obj = payload.get("data")
    if isinstance(data_obj, dict):
        for nested_k, nested_v in data_obj.items():
            if nested_k not in payload:
                flat[nested_k] = nested_v
            flat[f"data.{nested_k}"] = nested_v

    for k, v in payload.items():
        if isinstance(v, dict):
            for nested_k, nested_v in v.items():
                flat[f"{k}.{nested_k}"] = nested_v
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
    """Alias tablosu ile deterministik field mapping.

    Handles nested 'data' payloads from CRM webhooks:
    {"data": {"full_name": "Ali", "phone_number": "555", ...}, "event": "...", ...}
    """
    flat = _flatten_one_level(payload)

    mapped: dict[str, Any] = {}
    used_keys: set[str] = set()

    # İlk pas: dotted key'leri de dene (ör. "budget.range" → budget_range)
    for raw_key, value in flat.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue

        norm = _normalize_key(raw_key)
        target = _ALIAS_MAP.get(norm)

        # Soru işaretli key → soru işaretsiz de dene: "what_is_your_budget_range?" → "what_is_your_budget_range?"
        if not target:
            target = _ALIAS_MAP.get(norm.rstrip("?"))

        # Dotted key → compound alias dene: "budget.range" → "budget_range"
        if not target and "." in norm:
            compound = norm.replace(".", "_")
            target = _ALIAS_MAP.get(compound)
            if not target:
                target = _ALIAS_MAP.get(compound.rstrip("?"))

        if target and target not in mapped:
            if target == "budget_amount":
                parsed = _try_parse_int(value)
                if parsed is not None:
                    mapped[target] = parsed
                    used_keys.add(raw_key)
            else:
                mapped[target] = str(value).strip()
                used_keys.add(raw_key)

    # "platform" field'ı source olarak kullan (fb, ig, etc.)
    if "source" not in mapped:
        platform = flat.get("platform") or flat.get("data.platform")
        if platform:
            _PLATFORM_MAP = {"fb": "facebook", "ig": "instagram", "tt": "tiktok"}
            mapped["source"] = _PLATFORM_MAP.get(str(platform).lower(), str(platform))
            used_keys.add("platform")

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

    # Kalan alanları extra_fields'e koy
    extra: dict[str, Any] = {}

    # "data" içindeki alanları flat olarak extra'ya ekle (used olanlar hariç)
    data_obj = payload.get("data")
    if isinstance(data_obj, dict):
        for k, v in data_obj.items():
            if k not in used_keys and v is not None:
                extra[k] = v

    # Üst seviye alanları ekle (data hariç, used olanlar hariç)
    for k, v in payload.items():
        if k == "data":
            continue
        if k not in used_keys and v is not None:
            extra[k] = v

    # Nested key'lerin parent'larını temizle
    for uk in list(used_keys):
        if "." in uk:
            parent = uk.split(".")[0]
            extra.pop(parent, None)

    mapped["extra_fields"] = extra
    return FieldMappingResult(**mapped)


# ── LLM Mapper ──────────────────────────────────────────────────────────────

_FIELD_MAPPING_PROMPT = """\
Map this webhook JSON fields to our lead fields. DO NOT convert or interpret values — keep them as-is from the source.

Input:
```json
{json_payload}
```

Output JSON with these exact fields:
- full_name (string): person name
- phone (string): phone number
- email (string)
- city (string): city or country name from location/country fields
- source (string): map platform codes to readable names: ig=instagram, fb=facebook, tt=tiktok, li=linkedin, ws=whatsapp
- project_type (string): keep the ORIGINAL value as-is (e.g. "3+1_villa_with_private_pool", "studio_apartment", "2+1_penthouse"). Do NOT convert to categories.
- budget_range (string): keep the ORIGINAL value as-is (e.g. "£250,000-£450,000", "$100,000-$200,000"). Do NOT convert currency or categorize.
- budget_amount (int or null): only if an exact number is given, otherwise null
- decision_authority (string): keep as-is or empty
- timeline_urgency (string): keep as-is or empty
- notes (string): combine all form question answers into readable text (question: answer format)
- external_id (string): lead ID (from externalLeadId, id, or form_id)
- extra_fields (object): ad/campaign metadata and all remaining fields not mapped above

IMPORTANT: Your job is to MAP fields to the right place, NOT to interpret or convert values.

Return ONLY the JSON object, nothing else."""


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
    """Groq API ile field mapping (structured JSON output).

    Groq her zaman erişilebilir (cloud API), local LLM'e bağımlılık yok.
    """
    from groq import AsyncGroq
    from app.infrastructure.llm.groq_client import get_groq_client
    from app.config import get_settings

    settings = get_settings()
    json_str = json.dumps(payload, ensure_ascii=True, indent=2)
    prompt = _FIELD_MAPPING_PROMPT.format(json_payload=json_str)

    from app.infrastructure.llm.groq_rate_limiter import acquire
    if not await acquire(estimated_tokens=4000, priority="field_mapping"):
        log.warning("field_mapping_rate_limited")
        raise RuntimeError("Groq rate limit — field mapping skipped")

    client = get_groq_client()
    raw = ""
    try:
        response = await client.chat.completions.create(
            model=settings.qualification_judge_model,
            messages=[
                {"role": "system", "content": "You are a JSON field mapper. Return ONLY valid JSON, nothing else."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4096,
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"

        # Extract JSON from possible markdown wrapper
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            import re as _re
            m = _re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, _re.DOTALL)
            if m:
                cleaned = m.group(1)
        # Find first { ... } block
        brace_start = cleaned.find("{")
        brace_end = cleaned.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            cleaned = cleaned[brace_start:brace_end + 1]

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # LLM sometimes produces trailing commas or special chars
            # Try fixing common issues
            import re as _re
            fixed = _re.sub(r",\s*}", "}", cleaned)  # trailing comma
            fixed = _re.sub(r",\s*]", "]", fixed)  # trailing comma in array
            data = json.loads(fixed)
        if "extra_fields" not in data:
            data["extra_fields"] = {}
        # Filter to only known fields
        valid = {k: v for k, v in data.items() if k in FieldMappingResult.model_fields}
        if "extra_fields" not in valid:
            valid["extra_fields"] = data.get("extra_fields", {})
        return FieldMappingResult(**valid)
    except Exception as exc:
        log.warning("llm_field_mapping_failed", error=str(exc), raw=raw[:300])
        raise


# ── Orchestrator ─────────────────────────────────────────────────────────────

async def map_fields(payload: dict[str, Any]) -> FieldMappingResult:
    """Webhook payload'ını field'lara eşleştir.

    LLM-first yaklaşım:
    1. LLM ile tüm field'ları map et (semantic anlama: £135K → 500k_1m, studio_apartment → residential)
    2. LLM fail olursa → heuristic fallback
    """
    # Flatten data for LLM (data wrapper'ı varsa aç)
    # Extract lead-relevant fields only (skip system metadata to keep prompt short)
    _SKIP_KEYS = {
        "event", "entity", "webhookId", "occurredAt", "syncedAt",
        "rowIndex", "sheetName", "spreadsheetId", "updatedAt",
        "adId", "ad_id", "adsetId", "adset_id",
        "campaignId", "campaign_id", "userId", "isOrganic", "is_organic",
        "createdAt", "created_time", "createdTime",
    }
    llm_payload = payload
    if "data" in payload and isinstance(payload["data"], dict):
        llm_payload = {
            k: v for k, v in payload["data"].items()
            if k not in _SKIP_KEYS and v is not None
        }

    try:
        llm_result = await llm_map(llm_payload)
        log.info("field_mapping_llm",
                 name=bool(llm_result.full_name),
                 phone=bool(llm_result.phone))
        return llm_result
    except Exception as exc:
        log.warning("field_mapping_llm_failed_using_heuristic", error=str(exc))
        return heuristic_map(payload)
