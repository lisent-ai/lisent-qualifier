"""
Raw CRM webhook/project verisini LLM-friendly KB metnine dönüştürür.

Giriş: CRM'den gelen ham proje JSON'ı (webhook event veya doğrudan proje verisi)
Çıkış: Yapılandırılmış, okunabilir metin — prompt'a KB olarak enjekte edilir.

Pure string builder — no I/O.
"""
import json
import re
from typing import Any


# ── Facility name translations (EN → TR) ─────────────────────────────────────

_FACILITY_TR: dict[str, str] = {
    "swimming pool": "Yüzme Havuzu",
    "gym / fitness center": "Spor Salonu",
    "elevator": "Asansör",
    "playground": "Çocuk Oyun Alanı",
    "garden": "Bahçe",
    "clubhouse": "Kulüp Evi",
    "bbq area": "Mangal Alanı",
    "sports court": "Spor Sahası",
    "24/7 security": "7/24 Güvenlik",
    "wi-fi in common areas": "Ortak Alan Wi-Fi",
    "reception / concierge": "Resepsiyon / Kapıcı",
    "fireplace": "Şömine",
    "balcony": "Balkon",
    "terrace / patio": "Teras",
    "parking": "Otopark",
    "sauna": "Sauna",
    "spa": "Spa",
    "private pool": "Özel Havuz",
    "jacuzzi": "Jakuzi",
    "cinema room": "Sinema Odası",
    "walking paths": "Yürüyüş Yolları",
}

# ── Project type translations ─────────────────────────────────────────────────

_PROJECT_TYPE_TR: dict[str, str] = {
    "mixed-use": "Karma Kullanım",
    "residential": "Konut",
    "commercial": "Ticari",
    "villa": "Villa",
    "apartment": "Daire",
    "penthouse": "Penthouse",
    "townhouse": "Sıra Ev",
    "duplex": "Dubleks",
    "land": "Arsa",
}


def _normalize_sim_project(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize simplified simulator project format to CRM-like shape."""
    if "projectName" not in entry:
        return None

    project_type = entry.get("projectType") or ""
    delivery_date = entry.get("deliveryDate") or ""
    payment_plan = entry.get("paymentPlan") or ""
    investment_note = entry.get("investmentNote") or ""
    features = entry.get("features") or []
    if not isinstance(features, list):
        features = []

    facility_infos = [
        {
            "name": str(feature),
            "statusType": {"name": "active"},
        }
        for feature in features
        if isinstance(feature, str) and feature.strip()
    ]

    description_parts = [investment_note, payment_plan]
    description = " ".join(part for part in description_parts if part).strip()

    return {
        "name": entry.get("projectName", ""),
        "projectCode": entry.get("projectCode", ""),
        "location": entry.get("location", ""),
        "projectType": {"name": project_type} if project_type else "",
        "bio": description,
        "priceRange": entry.get("priceRange", ""),
        "projectFacilityInfos": facility_infos,
        "otherInfo": json.dumps({"deliveryDate": delivery_date}, ensure_ascii=False) if delivery_date else "",
        "paymentPlanText": payment_plan,
        "investmentNoteText": investment_note,
    }


def _safe_parse_json_string(val: Any) -> dict | list | None:
    """Nested JSON string'leri parse et (location, otherInfo gibi alanlar)."""
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _extract_location_text(location_raw: Any) -> str:
    """Location alanından okunabilir lokasyon metni çıkar."""
    parsed = _safe_parse_json_string(location_raw)
    if isinstance(parsed, dict):
        return parsed.get("location", "")
    if isinstance(location_raw, str) and not location_raw.startswith("{"):
        return location_raw
    return ""


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").replace("\r\n", " ").replace("\n", " ").split())


def _extract_other_info_map(raw: Any) -> dict[str, Any]:
    parsed = _safe_parse_json_string(raw)
    return parsed if isinstance(parsed, dict) else {}


def _is_valid_area_name(value: Any) -> bool:
    text = _clean_text(str(value or ""))
    if not text:
        return False
    if re.search(r"\bm2\b|m²", text.lower()):
        return False
    if re.fullmatch(r"[\d.,\s]+", text):
        return False
    return True


def _format_land_size(value: Any, language: str = "tr") -> str:
    text = _clean_text(str(value or ""))
    if not text:
        return ""
    if not re.fullmatch(r"[\d.,]+", text):
        return ""
    try:
        amount = float(text.replace(",", ""))
    except ValueError:
        return ""
    unit = "m²"
    return f"{amount:,.0f} {unit}"


def _normalize_distance_value(value: Any, language: str = "tr") -> str:
    text = _clean_text(str(value or ""))
    if not text:
        return ""
    low = text.lower()
    if any(token in low for token in ("hr", "hour", "hours", "saat")):
        return ""
    match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(km|m|min|mins|minute|minutes|dk|dakika)\b",
        low,
    )
    if not match:
        return ""
    amount = match.group(1).replace(",", ".")
    unit = match.group(2)
    if unit in ("min", "mins", "minute", "minutes", "dk", "dakika"):
        return f"{amount} {'dk' if language == 'tr' else 'min'}"
    return f"{amount} {unit}"


def _format_price(price: Any, currency_code: str = "") -> str:
    """Fiyatı okunabilir formata çevir."""
    if price is None:
        return ""
    try:
        amount = float(price)
    except (ValueError, TypeError):
        return str(price)

    if amount >= 1_000_000:
        formatted = f"{amount / 1_000_000:,.1f}M"
    elif amount >= 1_000:
        formatted = f"{amount:,.0f}"
    else:
        formatted = f"{amount:,.0f}"

    if currency_code:
        symbol_map = {
            "EUR": "€", "USD": "$", "GBP": "£", "TRY": "₺",
            "AED": "AED", "SAR": "SAR",
        }
        symbol = symbol_map.get(currency_code.upper(), currency_code)
        return f"{formatted} {symbol}"
    return formatted


def _translate_facility(name: str, language: str) -> str:
    if language == "tr":
        return _FACILITY_TR.get(name.lower().strip(), name)
    return name


def _translate_project_type(name: str, language: str) -> str:
    if language == "tr":
        return _PROJECT_TYPE_TR.get(name.lower().strip(), name)
    return name


def transform_project(data: dict[str, Any], language: str = "tr") -> str:
    """
    Tek bir CRM proje verisini okunabilir KB metnine dönüştür.

    data: CRM project JSON — doğrudan proje verisi veya webhook event'in "data" alanı.
    """
    tr = language.lower().strip() in ("tr", "turkish")

    name = (data.get("name") or "").strip()
    project_code = data.get("projectCode") or ""
    status = data.get("status") or ""

    # Project type
    pt = data.get("projectType")
    project_type_name = pt.get("name", "") if isinstance(pt, dict) else str(pt or "")
    project_type_display = _translate_project_type(project_type_name, language)

    # Location
    location_text = _extract_location_text(data.get("location"))

    # Price
    currency_code = ""
    currency = data.get("currency")
    if isinstance(currency, dict):
        currency_code = currency.get("code", "")
    price_from = data.get("priceFrom")
    price_text = _format_price(price_from, currency_code)
    price_range = data.get("priceRange") or ""
    if not price_text and price_range:
        price_text = str(price_range)

    # Other info (nested JSON string)
    other_info = _safe_parse_json_string(data.get("otherInfo")) or {}
    delivery_date = other_info.get("deliveryDate") or data.get("completionDate") or ""
    land_size = other_info.get("landSize") or ""
    distance_sea = other_info.get("distanceSea")
    distance_city = other_info.get("distanceCity")
    distance_airport = other_info.get("distanceAirport")

    # Totals
    total_properties = data.get("totalProperties") or ""
    sold_percentage = data.get("soldPercentage") or ""
    total_payment_plans = data.get("totalPaymentPlans") or ""
    payment_plan_text = data.get("paymentPlanText") or ""
    investment_note_text = data.get("investmentNoteText") or ""

    # Facilities — filter inactive and test entries
    facilities_raw = data.get("projectFacilityInfos") or []
    facility_names = []
    for f in facilities_raw:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        # Skip inactive facilities (CRM statusType.name check)
        st = f.get("statusType")
        if isinstance(st, dict) and st.get("name", "").lower() == "inactive":
            continue
        fname = f["name"].strip()
        # Skip test/admin entries (very short names, no known facility match)
        if len(fname) <= 6 and fname.lower() not in _FACILITY_TR:
            continue
        facility_names.append(_translate_facility(fname, language))

    # Build output
    title = name.upper()
    if project_code:
        title += f" ({project_code})"

    lines: list[str] = [f"## {title}"]

    if project_type_display:
        label = "Tür" if tr else "Type"
        lines.append(f"- {label}: {project_type_display}")

    if location_text:
        label = "Lokasyon" if tr else "Location"
        lines.append(f"- {label}: {location_text}")

    if price_text:
        label = "Başlangıç Fiyatı" if tr else "Starting Price"
        lines.append(f"- {label}: {price_text}")

    if delivery_date:
        label = "Teslim Tarihi" if tr else "Delivery Date"
        lines.append(f"- {label}: {delivery_date}")

    if total_properties:
        label = "Toplam Ünite" if tr else "Total Units"
        lines.append(f"- {label}: {total_properties}")

    if sold_percentage:
        label = "Satış Durumu" if tr else "Sales Status"
        lines.append(f"- {label}: {sold_percentage}")

    if total_payment_plans:
        label = "Ödeme Planı Seçeneği" if tr else "Payment Plan Options"
        lines.append(f"- {label}: {total_payment_plans}")

    if payment_plan_text:
        label = "Ödeme Planı" if tr else "Payment Plan"
        lines.append(f"- {label}: {payment_plan_text}")

    # Distances
    distance_parts = []
    if distance_sea:
        distance_parts.append(f"{'Denize' if tr else 'Sea'}: {distance_sea}")
    if distance_city:
        distance_parts.append(f"{'Şehre' if tr else 'City'}: {distance_city}")
    if distance_airport:
        distance_parts.append(f"{'Havalimanına' if tr else 'Airport'}: {distance_airport}")
    if distance_parts:
        label = "Mesafeler" if tr else "Distances"
        lines.append(f"- {label}: {', '.join(distance_parts)}")

    if facility_names:
        label = "Tesisler" if tr else "Facilities"
        lines.append(f"- {label}: {', '.join(facility_names)}")

    # Bio / description
    bio = (data.get("bio") or "").strip()
    if bio:
        lines.append(f"\n{bio}")
    elif investment_note_text:
        lines.append(f"\n{investment_note_text}")

    return "\n".join(lines)


def _extract_project_data(entry: dict[str, Any]) -> dict[str, Any] | None:
    """
    Farklı formatlardan proje verisini çıkar:
    1. Doğrudan proje verisi (name, projectCode alanları var)
    2. Webhook event (data.name var, entity: "projects")
    3. CRM webhook-data entry (payload.data.name var)
    """
    # Format 0: simplified simulator project format
    normalized = _normalize_sim_project(entry)
    if normalized:
        return normalized

    # Format 4: RAG entity — { content, metadata: { name, ... }, entity_type: "projects" }
    if entry.get("entity_type") == "projects" and isinstance(entry.get("metadata"), dict):
        meta = entry["metadata"]
        if meta.get("name"):
            return meta

    # Format 1: Doğrudan proje verisi
    if "name" in entry and ("projectCode" in entry or "projectType" in entry
                            or "priceFrom" in entry or "projectFacilityInfos" in entry):
        return entry

    # Format 2: Webhook event — { data: {...}, event: "...", entity: "projects" }
    if "data" in entry and isinstance(entry.get("data"), dict):
        inner = entry["data"]
        if "name" in inner and ("projectCode" in inner or "projectType" in inner
                                or "priceFrom" in inner):
            return inner

    # Format 3: CRM API response — { id: "...", payload: { data: {...}, entity: "projects" } }
    payload = entry.get("payload")
    if isinstance(payload, dict):
        return _extract_project_data(payload)  # Recurse into payload

    return None


def transform_webhook_entries(
    entries: list[dict[str, Any]] | dict[str, Any],
    language: str = "tr",
) -> str:
    """
    Bir veya birden fazla webhook entry'sini KB metnine dönüştür.

    entries: Tek bir webhook event dict'i veya bir liste.
    """
    if isinstance(entries, dict):
        # Handle RAG batch format: {"data": [...], "entity_type": "..."}
        if isinstance(entries.get("data"), list):
            entries = entries["data"]
        else:
            entries = [entries]

    projects: list[str] = []
    seen_ids: set[str] = set()

    for entry in entries:
        project_data = _extract_project_data(entry)
        if not project_data:
            continue

        # Deduplicate by project id or code
        pid = project_data.get("id") or project_data.get("projectCode") or ""
        if pid and pid in seen_ids:
            continue
        if pid:
            seen_ids.add(pid)

        transformed = transform_project(project_data, language)
        if transformed:
            projects.append(transformed)

    if not projects:
        return ""

    tr = language.lower().strip() in ("tr", "turkish")
    header = "# Şirket Proje Portföyü" if tr else "# Company Project Portfolio"
    return header + "\n\n" + "\n\n".join(projects)


# ── RAG structured data (projects.json + properties.json) ─────────────────────


def _format_rag_price(price: Any) -> str:
    """Format price for RAG display — full number with EUR symbol."""
    try:
        amount = float(price)
    except (ValueError, TypeError):
        return str(price) if price else ""
    if amount <= 0:
        return ""
    return f"{amount:,.0f} €"


def _simplify_location(full_location: str) -> str:
    """Extract concise location from full address string."""
    if not full_location:
        return ""
    parts = [p.strip() for p in full_location.split(",") if p.strip()]

    district = ""
    municipality = ""
    meaningful: list[str] = []
    skip_lower = ("villas", "sitesi", "homes", "resort", "bay ", "gardens")

    for p in parts:
        low = p.lower().strip()
        if low in ("cyprus", "northern cyprus"):
            continue
        if re.match(r"^\d{4,5}$", low):
            continue
        if "district" in low:
            district = re.sub(r"\s*district.*", "", p, flags=re.IGNORECASE).strip()
            break
        if "belediyesi" in low:
            municipality = re.sub(r"\s*belediyesi.*", "", p, flags=re.IGNORECASE).strip()
            continue
        if re.match(r"^[A-Z]\.\d", p.strip()):
            continue
        if any(w in low for w in skip_lower):
            continue
        meaningful.append(p.strip())

    # Prefer 2nd part (area) over 1st (micro-locality); fallback to municipality
    if len(meaningful) >= 2:
        area = meaningful[1]
    elif municipality:
        area = municipality
    elif meaningful:
        area = meaningful[0]
    else:
        area = ""

    if area and district and area.lower().strip() != district.lower().strip():
        return f"{area}, {district}"
    return area or district or (parts[0] if parts else "")


def _infer_unit_type_from_description(description: str) -> str:
    """Infer human-readable unit type from property description."""
    if not description:
        return ""
    lowered = description.lower()
    # Pattern: "ProjectName – TypeName (by Company)? verb..."
    m = re.search(
        r"–\s*(.+?)(?:\s+by\s+\w|\s+offers\b|\s+is\b|\s+provides\b"
        r"|\s+combines\b|\s+represents\b|\s+redefines\b)",
        description,
    )
    if m:
        return m.group(1).strip()
    if "studio" in lowered and "penthouse" in lowered:
        return "Studio Penthouse"
    if "studio" in lowered and "garden" in lowered:
        return "Studio Garden"
    if re.search(r"\bvilla\b", description, re.IGNORECASE):
        return "Villa"
    if re.search(r"\bpenthouse\b", description, re.IGNORECASE):
        return "Penthouse"
    if re.search(r"\bstudio\b", description, re.IGNORECASE):
        return "Studio"
    if re.search(r"\bbungalow\b", description, re.IGNORECASE):
        return "Bungalow"
    if re.search(r"\btownhouse\b", description, re.IGNORECASE):
        return "Townhouse"
    return ""


def _normalize_unit_type_name(raw: str) -> str:
    """Normalize similar type names (e.g. 'Studio Garden' ≈ 'Garden Studio')."""
    low = raw.lower().strip()
    # Merge studio variants
    if "studio" in low and "garden" in low:
        return "Studio Garden"
    if "studio" in low and "penthouse" in low:
        return "Studio Penthouse"
    if "penthouse" in low and "studio" in low:
        return "Studio Penthouse"
    return raw


def _group_properties_by_project(
    properties_data: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group RAG properties by projectId. Returns {projectId: [property_meta]}."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in properties_data:
        meta = item.get("metadata") or {}
        pid = meta.get("projectId", "")
        if not pid:
            continue
        prop = dict(meta)
        prop["_description"] = meta.get("description", "")
        groups.setdefault(pid, []).append(prop)
    return groups


def _summarize_project_units(
    properties: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize properties: price range, unit types, count."""
    prices: list[float] = []
    unit_types: dict[str, list[float]] = {}
    unit_highlights: dict[str, list[str]] = {}

    for prop in properties:
        desc = prop.get("_description") or prop.get("description", "")
        raw_type = _infer_unit_type_from_description(desc)
        unit_type = _normalize_unit_type_name(raw_type) if raw_type else "Other"

        try:
            price = float(prop.get("priceFrom", 0))
        except (ValueError, TypeError):
            price = 0.0

        if price > 0:
            prices.append(price)
            unit_types.setdefault(unit_type, []).append(price)

        highlights = _extract_unit_highlights(desc, language="tr")
        if highlights:
            bucket = unit_highlights.setdefault(unit_type, [])
            for item in highlights:
                if item not in bucket:
                    bucket.append(item)

    price_min = min(prices) if prices else 0.0
    price_max = max(prices) if prices else 0.0

    type_summaries: list[str] = []
    type_highlights: list[str] = []
    for utype, type_prices in sorted(unit_types.items()):
        tmin, tmax = min(type_prices), max(type_prices)
        if tmin == tmax:
            type_summaries.append(f"{utype} ({_format_rag_price(tmin)})")
        else:
            type_summaries.append(
                f"{utype} ({_format_rag_price(tmin)} – {_format_rag_price(tmax)})"
            )

    for utype, highlights in unit_highlights.items():
        if not highlights:
            continue
        type_highlights.append(f"{utype}: {', '.join(highlights[:2])}")

    return {
        "count": len(properties),
        "price_min": price_min,
        "price_max": price_max,
        "type_summaries": type_summaries,
        "type_highlights": type_highlights,
        "type_names": list(unit_types.keys()),
    }


def _extract_key_features(bio: str) -> str:
    """Extract 2-3 key amenities from project bio text."""
    if not bio:
        return ""
    bio_lower = bio.lower()
    keywords = [
        ("private pool", "özel havuz"),
        ("infinity pool", "sonsuzluk havuzu"),
        ("communal pool", "ortak havuz"),
        ("swimming pool", "yüzme havuzu"),
        ("sea view", "deniz manzarası"),
        ("mountain view", "dağ manzarası"),
        ("beachfront", "sahil kenarı"),
        ("private beach", "özel plaj"),
        ("beach", "plaj"),
        ("spa", "spa"),
        ("hammam", "hamam"),
        ("gym", "fitness"),
        ("fitness", "fitness"),
        ("restaurant", "restoran"),
        ("garden", "bahçe"),
        ("terrace", "teras"),
        ("tennis", "tenis kortu"),
        ("cinema", "sinema"),
        ("walking path", "yürüyüş yolu"),
    ]
    features: list[str] = []
    seen: set[str] = set()
    for en_key, tr_val in keywords:
        if en_key in bio_lower and tr_val not in seen:
            features.append(tr_val)
            seen.add(tr_val)
        if len(features) >= 3:
            break
    return ", ".join(features)


def _extract_unit_highlights(description: str, language: str = "tr") -> list[str]:
    """Extract a few customer-facing unit highlights from property descriptions."""
    if not description:
        return []
    desc = description.lower()
    keyword_pairs = [
        ("private infinity pool", "özel infinity havuz" if language == "tr" else "private infinity pool"),
        ("private pool", "özel havuz" if language == "tr" else "private pool"),
        ("rooftop terrace", "çatı terası" if language == "tr" else "rooftop terrace"),
        ("private terrace", "özel teras" if language == "tr" else "private terrace"),
        ("garden terrace", "bahçe terası" if language == "tr" else "garden terrace"),
        ("private garden", "özel bahçe" if language == "tr" else "private garden"),
        ("private outdoor space", "özel dış alan" if language == "tr" else "private outdoor space"),
        ("sea view", "deniz manzarası" if language == "tr" else "sea view"),
        ("mountain view", "dağ manzarası" if language == "tr" else "mountain view"),
        ("panoramic", "panoramik manzara" if language == "tr" else "panoramic views"),
        ("high ceilings", "yüksek tavan" if language == "tr" else "high ceilings"),
        ("duplex", "dubleks plan" if language == "tr" else "duplex layout"),
        ("open-plan", "açık plan yaşam alanı" if language == "tr" else "open-plan living"),
        ("floor-to-ceiling", "geniş cam açıklıkları" if language == "tr" else "floor-to-ceiling windows"),
        ("fitted kitchen", "modern mutfak" if language == "tr" else "fitted kitchen"),
        ("walk-in closets", "giyinme alanı" if language == "tr" else "walk-in closets"),
    ]
    results: list[str] = []
    seen: set[str] = set()
    for needle, label in keyword_pairs:
        if needle in desc and label not in seen:
            results.append(label)
            seen.add(label)
        if len(results) >= 3:
            break
    return results


def transform_rag_portfolio(
    projects_data: dict[str, Any] | list[dict[str, Any]],
    properties_data: dict[str, Any] | list[dict[str, Any]],
    language: str = "tr",
) -> str:
    """
    Transform RAG projects + properties into concise KB markdown.

    projects_data:  {"data": [...]} or [...]
    properties_data: {"data": [...]} or [...]
    Returns markdown text suitable for the KB section.
    """
    tr = language.lower().strip() in ("tr", "turkish")

    projects_list = (
        projects_data.get("data", [])
        if isinstance(projects_data, dict)
        else projects_data
    )
    props_list = (
        properties_data.get("data", [])
        if isinstance(properties_data, dict)
        else properties_data
    )

    prop_groups = _group_properties_by_project(props_list)

    parsed: list[dict[str, Any]] = []
    for item in projects_list:
        meta = item.get("metadata") or item
        if not meta.get("name"):
            continue
        parsed.append(meta)
    parsed.sort(key=lambda p: p.get("sortNumber", 99))

    sections: list[str] = []
    for proj in parsed:
        pid = proj.get("id", "")
        name = (proj.get("name") or "").strip()
        code = proj.get("projectCode") or ""
        bio = (proj.get("bio") or "").strip()

        location_text = _extract_location_text(proj.get("location"))
        location_short = _simplify_location(location_text)

        other_info = _extract_other_info_map(proj.get("otherInfo"))
        delivery_date = other_info.get("deliveryDate") or ""
        if not delivery_date:
            comp = proj.get("completionDate") or ""
            if comp:
                delivery_date = comp[:7]  # "2027-12-01" → "2027-12"
        area_name = (
            other_info.get("exactAreaName")
            if _is_valid_area_name(other_info.get("exactAreaName"))
            else ""
        )
        land_size = _format_land_size(other_info.get("landSize"), language)
        distance_sea = _normalize_distance_value(other_info.get("distanceSea"), language)
        distance_city = _normalize_distance_value(other_info.get("distanceCity"), language)
        distance_airport = _normalize_distance_value(other_info.get("distanceAirport"), language)
        distance_university = _normalize_distance_value(other_info.get("distanceUniversity"), language)

        props = prop_groups.get(pid, [])
        unit_info = _summarize_project_units(props)

        title = name.upper()
        if code:
            title += f" ({code})"
        lines: list[str] = [f"## {title}"]

        if location_short:
            label = "Lokasyon" if tr else "Location"
            lines.append(f"- {label}: {location_short}")
        if area_name:
            label = "Bölge" if tr else "Area"
            lines.append(f"- {label}: {area_name}")

        if unit_info["price_min"] > 0:
            if unit_info["price_min"] == unit_info["price_max"]:
                ptxt = _format_rag_price(unit_info["price_min"])
            else:
                ptxt = (
                    f"{_format_rag_price(unit_info['price_min'])} – "
                    f"{_format_rag_price(unit_info['price_max'])}"
                )
            label = "Fiyat Aralığı" if tr else "Price Range"
            lines.append(f"- {label}: {ptxt}")
        elif proj.get("priceFrom"):
            label = "Başlangıç Fiyatı" if tr else "Starting Price"
            lines.append(f"- {label}: {_format_rag_price(proj['priceFrom'])}")

        if delivery_date:
            label = "Teslim" if tr else "Delivery"
            lines.append(f"- {label}: {delivery_date}")
        if land_size:
            label = "Arsa / Proje Alanı" if tr else "Land Size"
            lines.append(f"- {label}: {land_size}")

        dist_parts: list[str] = []
        if distance_sea:
            dist_parts.append(f"{'Denize' if tr else 'Sea'}: {distance_sea}")
        if distance_city:
            dist_parts.append(f"{'Şehre' if tr else 'City'}: {distance_city}")
        if distance_airport:
            dist_parts.append(f"{'Havalimanına' if tr else 'Airport'}: {distance_airport}")
        if distance_university:
            dist_parts.append(f"{'Üniversiteye' if tr else 'University'}: {distance_university}")
        if dist_parts:
            lines.append(f"- {' | '.join(dist_parts)}")

        if unit_info["type_names"]:
            label = "Ünite Tipleri" if tr else "Unit Types"
            lines.append(f"- {label}: {', '.join(unit_info['type_names'])}")
        if unit_info.get("type_highlights"):
            label = "Ünite Notları" if tr else "Unit Notes"
            lines.append(f"- {label}: {'; '.join(unit_info['type_highlights'][:3])}")

        if unit_info["count"]:
            label = "Mevcut Ünite" if tr else "Available Units"
            lines.append(f"- {label}: {unit_info['count']}")

        if bio:
            short = bio[:250].rsplit(" ", 1)[0] if len(bio) > 250 else bio
            short = short.replace("\r\n", " ").replace("\n", " ").strip()
            lines.append(f"\n{short}")

        sections.append("\n".join(lines))

    if not sections:
        return ""
    header = "# Şirket Proje Portföyü" if tr else "# Company Project Portfolio"
    return header + "\n\n" + "\n\n".join(sections)


# ── RAG matched projects builder ──────────────────────────────────────────────


def _parse_budget_range(budget_str: str) -> tuple[float, float]:
    """Parse budget range string into (min, max)."""
    if not budget_str:
        return (0.0, 0.0)
    cleaned = re.sub(r"[€$£₺,\s]", "", budget_str)
    cleaned = re.sub(r"(euro|eur|usd|gbp|tl|try)", "", cleaned, flags=re.IGNORECASE)
    numbers = re.findall(r"[\d.]+", cleaned)
    values: list[float] = []
    for n in numbers:
        try:
            values.append(float(n))
        except ValueError:
            pass
    if not values:
        return (0.0, 0.0)
    return (min(values), max(values))


def build_rag_matched_section(
    lead_json: dict[str, Any],
    projects_data: dict[str, Any] | list[dict[str, Any]],
    properties_data: dict[str, Any] | list[dict[str, Any]],
    language: str = "tr",
    messages: list[dict[str, Any]] | None = None,
) -> str:
    """Build matched_projects_section from structured RAG data."""
    tr = language.lower().strip() in ("tr", "turkish")

    projects_list = (
        projects_data.get("data", [])
        if isinstance(projects_data, dict)
        else projects_data
    )
    props_list = (
        properties_data.get("data", [])
        if isinstance(properties_data, dict)
        else properties_data
    )
    if not projects_list:
        return ""

    # Extract lead preferences
    form = (
        lead_json.get("form_data")
        if isinstance(lead_json.get("form_data"), dict)
        else {}
    )
    project_type_raw = (
        str(
            lead_json.get("project_type", "")
            or form.get("what_type_of_property_are_you_interested_in?", "")
            or form.get("what_type_of_property_are_you_interested_in", "")
        )
        .lower()
        .replace("_", " ")
        .strip()
    )
    budget_raw = str(
        lead_json.get("budget_range", "")
        or form.get("what_is_your_budget_range?", "")
        or form.get("what_is_your_budget_range", "")
    ).lower().strip()
    purpose_raw = str(
        form.get("why_are_you_interested_in_north_cyprus?", "")
        or form.get("purpose", "")
        or lead_json.get("notes", "")
    ).lower().replace("_", " ").strip()
    location_raw = str(
        form.get("preferred_location", "")
        or lead_json.get("city", "")
    ).lower().strip()

    recent_user_text = ""
    if messages:
        user_parts = [
            str(message.get("content", "")).lower().strip()
            for message in messages
            if str(message.get("role", "")).lower() == "user" and message.get("content")
        ]
        recent_user_text = " ".join(user_parts[-6:])

    if recent_user_text:
        project_type_raw = f"{project_type_raw} {recent_user_text}".strip()
        purpose_raw = f"{purpose_raw} {recent_user_text}".strip()
        # Do NOT append conversation text to location — prevents false matches
        # (e.g., "villa" in user text matching "Caesar Bay Villas" in address)
        if not budget_raw:
            budget_raw = recent_user_text

    # Detect explicit villa negation: "büyük villa değil", "villa istemiyorum" etc.
    negates_villa = any(
        token in recent_user_text
        for token in (
            "villa değil", "villa degil", "büyük villa değil", "buyuk villa degil",
            "villa istemiyorum", "villa almak istemiyorum",
        )
    )

    recent_type_preference = ""
    if any(token in recent_user_text for token in ("studio", "stüdyo")):
        recent_type_preference = "studio"
    elif "penthouse" in recent_user_text:
        recent_type_preference = "penthouse"
    elif "villa" in recent_user_text and not negates_villa:
        recent_type_preference = "villa"

    prefers_studio_over_villa = (
        any(token in recent_user_text for token in ("villa yerine", "villa almak yerine"))
        and any(token in recent_user_text for token in ("studio", "stüdyo"))
    )

    # Detect "smaller / affordable / multiple units" preference from conversation
    prefers_affordable = any(
        token in recent_user_text
        for token in (
            "küçük", "kucuk", "uygun fiyat", "ucuz", "birkaç ev", "birkac ev",
            "birkaç daire", "birkac daire", "birden fazla", "çok sayıda", "cok sayida",
            "büyük villa değil", "buyuk villa degil", "daha küçük", "daha kucuk",
        )
    )

    budget_min, budget_max = _parse_budget_range(budget_raw)
    prop_groups = _group_properties_by_project(props_list)

    scored: list[dict[str, Any]] = []
    for item in projects_list:
        meta = item.get("metadata") or item
        if not meta.get("name"):
            continue
        pid = meta.get("id", "")
        name_low = (meta.get("name") or "").lower()
        bio_low = (meta.get("bio") or "").lower()
        loc_low = _extract_location_text(meta.get("location", "")).lower()

        props = prop_groups.get(pid, [])
        unit_info = _summarize_project_units(props)
        available_types_str = " ".join(unit_info.get("type_names", [])).lower()

        score = 0
        reasons: list[str] = []

        # ── Type matching ─────────────────────────────────────────
        if "villa" in project_type_raw and not negates_villa:
            if "villa" in name_low or "villa" in available_types_str:
                score += 5
                reasons.append("Villa tercihi" if tr else "Villa preference")
        elif negates_villa and ("villa" in name_low or "villa" in available_types_str):
            # User explicitly said "not villa" — penalize villa-only projects
            if all("villa" in t.lower() for t in unit_info.get("type_names", [""])):
                score -= 5
        if any(t in project_type_raw for t in ("daire", "apartment", "rezidans")):
            if "apartment" in available_types_str:
                score += 5
                reasons.append("Daire tercihi" if tr else "Apartment preference")
        if "penthouse" in project_type_raw:
            if "penthouse" in available_types_str:
                score += 5
                reasons.append("Penthouse tercihi" if tr else "Penthouse preference")
        if "studio" in project_type_raw:
            if "studio" in available_types_str:
                score += 4
        if recent_type_preference == "studio":
            if "studio" in available_types_str:
                score += 7
                reasons.append("Güncel mesajda studio ilgisi" if tr else "Recent studio interest")
            elif prefers_studio_over_villa:
                score -= 4
        elif recent_type_preference == "penthouse" and "penthouse" in available_types_str:
            score += 5
            reasons.append("Güncel mesajda penthouse ilgisi" if tr else "Recent penthouse interest")
        elif recent_type_preference == "villa":
            if "villa" in available_types_str or "villa" in name_low:
                score += 5
                reasons.append("Güncel mesajda villa ilgisi" if tr else "Recent villa interest")
        # Room pattern (3+1, 4+1)
        room_match = re.search(r"(\d\+\d)", project_type_raw)
        if room_match:
            pattern = room_match.group(1)
            if pattern in available_types_str:
                score += 3
        # Pool bonus
        if "havuz" in project_type_raw or "pool" in project_type_raw:
            if "pool" in bio_low or "havuz" in bio_low:
                score += 2

        # ── Affordable / multi-unit preference ────────────────────
        if prefers_affordable:
            p_min = unit_info["price_min"]
            if p_min and p_min < 175_000:
                score += 8
                reasons.append(
                    "Uygun fiyat bandı" if tr else "Affordable price range"
                )
            elif p_min and p_min < 250_000:
                score += 6
                reasons.append(
                    "Uygun fiyat bandı" if tr else "Affordable price range"
                )
            elif p_min and p_min < 400_000:
                score += 3
            elif p_min and p_min >= 600_000:
                score -= 4  # Penalize expensive projects when user wants affordable

        # ── Budget matching ───────────────────────────────────────
        if budget_min > 0 or budget_max > 0:
            p_min = unit_info["price_min"]
            p_max = unit_info["price_max"]
            if p_min and p_max:
                # When user prefers affordable/multi-unit, form budget is irrelevant —
                # they want per-unit affordability, not total budget match
                budget_weight = 0 if prefers_affordable else 4
                if budget_max >= p_min and budget_min <= p_max:
                    score += budget_weight
                    reasons.append("Bütçe uyumu" if tr else "Budget match")
                elif budget_min <= p_max * 1.3:
                    score += 1

        # ── Location matching ─────────────────────────────────────
        if location_raw:
            loc_tokens = re.split(r"[/,\s]+", location_raw)
            for tok in loc_tokens:
                if tok and len(tok) >= 3 and tok in loc_low:
                    score += 5
                    reasons.append(
                        f"{tok.title()} lokasyonu" if tr else f"{tok.title()} location"
                    )
                    break

        # ── Purpose matching ──────────────────────────────────────
        if any(w in purpose_raw for w in ("yatırım", "investment", "kira", "rental")):
            if any(w in bio_low for w in ("investment", "rental", "kira", "yatırım")):
                score += 3
                reasons.append("Yatırım amacı" if tr else "Investment purpose")
        if any(w in purpose_raw for w in ("tatil", "holiday", "vacation")):
            if any(w in bio_low for w in ("holiday", "tatil", "resort", "beach")):
                score += 3
                reasons.append("Tatil amacı" if tr else "Holiday purpose")

        scored.append({
            "score": score,
            "meta": meta,
            "unit_info": unit_info,
            "reasons": reasons,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0] if scored else None

    if not best or best["score"] <= 0:
        no_match = (
            "[BU LEAD İÇİN UYGUN PROJELER]\n"
            "- Net bir proje eşleşmesi görünmüyor.\n"
            "- Proje uydurma. Önce amaç, bölge ve bütçe sinyalini netleştir.\n"
        ) if tr else (
            "[BEST MATCHING PROJECTS FOR THIS LEAD]\n"
            "- No clear project match yet.\n"
            "- Do not invent a project. Clarify purpose, location, and budget first.\n"
        )
        return no_match

    meta = best["meta"]
    unit_info = best["unit_info"]
    name = meta.get("name", "")
    loc_short = _simplify_location(_extract_location_text(meta.get("location", "")))
    reason_text = " + ".join(best["reasons"][:3])
    bio = (meta.get("bio") or "").strip()
    features = _extract_key_features(bio)
    other_info = _extract_other_info_map(meta.get("otherInfo"))
    area_name = (
        other_info.get("exactAreaName")
        if _is_valid_area_name(other_info.get("exactAreaName"))
        else ""
    )
    delivery_date = other_info.get("deliveryDate") or str(meta.get("completionDate") or "")[:7]
    land_size = _format_land_size(other_info.get("landSize"), language)
    distance_sea = _normalize_distance_value(other_info.get("distanceSea"), language)
    distance_city = _normalize_distance_value(other_info.get("distanceCity"), language)
    distance_airport = _normalize_distance_value(other_info.get("distanceAirport"), language)

    if unit_info["price_min"] > 0:
        if unit_info["price_min"] == unit_info["price_max"]:
            price_text = _format_rag_price(unit_info["price_min"])
        else:
            price_text = (
                f"{_format_rag_price(unit_info['price_min'])} – "
                f"{_format_rag_price(unit_info['price_max'])}"
            )
    else:
        price_text = _format_rag_price(meta.get("priceFrom", ""))

    if tr:
        lines = ["[BU LEAD İÇİN UYGUN PROJELER]"]
        lines.append(f"- En yakın proje: {name}")
        if loc_short:
            lines.append(f"- Lokasyon: {loc_short}")
        if area_name:
            lines.append(f"- Bölge: {area_name}")
        if delivery_date:
            lines.append(f"- Teslim: {delivery_date}")
        if land_size:
            lines.append(f"- Arsa / proje alanı: {land_size}")
        if reason_text:
            lines.append(f"- Uyum nedeni: {reason_text}")
        dist_parts: list[str] = []
        if distance_sea:
            dist_parts.append(f"Denize: {distance_sea}")
        if distance_city:
            dist_parts.append(f"Şehre: {distance_city}")
        if distance_airport:
            dist_parts.append(f"Havalimanına: {distance_airport}")
        if dist_parts:
            lines.append(f"- Yakınlık: {' | '.join(dist_parts)}")
        if unit_info["type_names"]:
            lines.append(f"- Mevcut tipler: {', '.join(unit_info['type_names'])}")
        if unit_info.get("type_highlights"):
            lines.append(f"- Ünite detayları: {'; '.join(unit_info['type_highlights'][:3])}")
        if unit_info["type_summaries"]:
            lines.append(
                f"- Tip / fiyat özeti: {'; '.join(unit_info['type_summaries'][:4])}"
            )
        if features:
            lines.append(f"- Öne çıkan: {features}")
        if price_text:
            lines.append(f"- Fiyat bilgisi (yalnızca sorarsa): {price_text}")
        lines.append(
            f"- Doğal kısa özet: {name} tarafında "
            f"{', '.join(unit_info['type_names'][:4]) if unit_info['type_names'] else 'uygun seçenekler'} "
            f"var; fiyatlar {price_text or 'projeye göre değişiyor'} bandında ilerliyor."
        )
        lines.append(
            "- Kural: tek seferde sadece 1 proje ve en fazla 1-2 özellik söyle."
        )
        lines.append(
            "- Kullanıcı 'seçenekler ne / properties / hangi üniteler var' derse sadece doğrulanmış tip adlarını ve varsa tip bazlı fiyat özetini söyle; m2, tesis veya link UYDURMA."
        )
        lines.append(
            "- Kullanıcı tesis / imkan sorarsa sadece bu bölümde veya KB'de açıkça görünen özellikleri söyle. Kullanıcı link / broşür isterse doğrulanmış link yoksa 'kontrol edip paylaşayım' de."
        )
        lines.append(
            "- Kullanıcı mesafe / yakınlık sorarsa sadece burada görünen temiz verileri kullan. Kullanıcı teras, bahçe, manzara, yüksek tavan gibi detaylar sorarsa yalnızca ünite açıklamalarında açıkça geçenleri söyle."
        )
        if len(scored) > 1 and scored[1]["score"] > 0:
            alt = scored[1]["meta"]
            alt_name = alt.get("name", "")
            alt_loc = _simplify_location(
                _extract_location_text(alt.get("location", ""))
            )
            lines.append(f"- Alternatif: {alt_name} ({alt_loc})")
        return "\n".join(lines)

    # English
    lines = ["[BEST MATCHING PROJECTS FOR THIS LEAD]"]
    lines.append(f"- Best project: {name}")
    if loc_short:
        lines.append(f"- Location: {loc_short}")
    if reason_text:
        lines.append(f"- Why it fits: {reason_text}")
    if unit_info["type_names"]:
        lines.append(f"- Available types: {', '.join(unit_info['type_names'])}")
    if features:
        lines.append(f"- Key features: {features}")
    if price_text:
        lines.append(f"- Price (only if asked): {price_text}")
    lines.append("- Rule: mention only one project and keep it to 1-2 key features.")
    if len(scored) > 1 and scored[1]["score"] > 0:
        alt = scored[1]["meta"]
        alt_name = alt.get("name", "")
        alt_loc = _simplify_location(
            _extract_location_text(alt.get("location", ""))
        )
        lines.append(f"- Alternative: {alt_name} ({alt_loc})")
    return "\n".join(lines)


# ── Reference (sold-out) projects ─────────────────────────────────────────────


_CATEGORY_TR: dict[str, str] = {
    "apartment": "Daire",
    "villa": "Villa",
    "penthouse": "Penthouse",
    "resort_residence": "Resort",
    "residential_development": "Konut",
    "studio": "Stüdyo",
}


def transform_reference_projects(
    data: list[dict[str, Any]],
    language: str = "tr",
) -> str:
    """Transform sold-out reference projects into a compact KB section."""
    if not data:
        return ""
    tr = language.lower().strip() in ("tr", "turkish")

    if tr:
        lines = [
            "## REFERANS PROJELER (Satışı Tamamlanmış)",
            "Aşağıdaki projeler şirketin daha önce tamamlayıp sattığı projelerdir.",
            "Bu projeler SATIŞTA DEĞİLDİR ve müşteriye ÖNERİLEMEZ.",
            "Sadece müşteri kendisi sorduğunda kısa bilgi ver ve benzer AKTİF projelere yönlendir.",
            "",
        ]
    else:
        lines = [
            "## REFERENCE PROJECTS (Sold Out)",
            "These are past projects completed and sold by the company.",
            "They are NOT for sale. Only mention if the customer asks, then redirect to active projects.",
            "",
        ]

    for proj in data:
        name = proj.get("project_name", "")
        if not name:
            continue
        loc = proj.get("location") or {}
        loc_str = f"{loc.get('city', '')}, {loc.get('district', '')}".strip(", ")
        cats = proj.get("property_category") or []
        cat_str = ", ".join(
            _CATEGORY_TR.get(c, c.replace("_", " ").title()) for c in cats
        )
        rooms = proj.get("room_types") or []
        room_str = "/".join(rooms) if rooms else ""
        notes = proj.get("notes") or ""

        parts = [f"- **{name}**"]
        if loc_str:
            parts.append(loc_str)
        if cat_str:
            parts.append(cat_str)
        if room_str:
            parts.append(room_str)
        tag = "SATILDI" if tr else "SOLD OUT"
        parts.append(tag)
        line = " | ".join(parts)
        if notes:
            line += f"  ({notes})"
        lines.append(line)

    return "\n".join(lines)


def extract_all_project_names(
    active_projects: dict[str, Any] | list[dict[str, Any]] | None = None,
    reference_projects: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Extract all project names (active + reference) for mention detection."""
    names: list[str] = []
    if active_projects:
        active_list = (
            active_projects.get("data", [])
            if isinstance(active_projects, dict)
            else active_projects
        )
        for item in active_list:
            meta = item.get("metadata") or item
            n = meta.get("name", "")
            if n:
                names.append(n)
    if reference_projects:
        for proj in reference_projects:
            n = proj.get("project_name", "")
            if n:
                names.append(n)
    return names
