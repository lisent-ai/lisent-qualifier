"""
Generic prompt builders — delegates to language/sector-specific templates.

All functions are pure string builders. No I/O.
"""
import json
import unicodedata
from typing import Any

from app.domain.conversation.templates.registry import TemplateRegistry


# ── Gap-aware CHAMP routing ──────────────────────────────────────────────────

_GAP_HINTS = {
    "tr": {
        "all_missing": "Tüm boyutlar eksik. Öncelik: bunu daha çok yatırım için mi, kullanım için mi düşündüğünü anla.",
        "challenges": "Önce amaç ve kullanım şeklini anlamaya çalış; sonra mülk tercihini netleştir.",
        "authority": "Karar verici kim? Kiminle birlikte karar veriyorlar?",
        "money": "Bütçe veya finansal hazırlık hakkında sinyal topla.",
        "prioritization": "Ne zaman ilerlemek istiyor? Zamanlama sinyali al.",
        "prefix": "Eksik alan: {dim} ({score}/25). {hint}",
    },
    "en": {
        "all_missing": "All CHAMP dimensions are missing. Priority: Challenges (what do they want to build?)",
        "challenges": "What type of project are they considering? Ask for details.",
        "authority": "Who is the decision maker? Who else is involved?",
        "money": "Get information about their budget.",
        "prioritization": "When do they want to start? Ask about urgency.",
        "prefix": "Biggest gap: {dim} (score: {score}/25). {hint}",
    },
}


# ── Tone instruction maps ───────────────────────────────────────────────────

_TONE_MAP_TR = {
    "professional": "Profesyonel, resmi ve güvenilir bir dil kullan.",
    "casual": "Sıcak, samimi ve rahat bir sohbet tonu kullan.",
    "technical": "Sektöre özgü teknik terimler ve profesyonel jargon kullan.",
    "luxury": "Premium, sofistike ve özel bir dil kullan. Müşteriye ayrıcalıklı hissettir.",
}

_TONE_MAP_EN = {
    "professional": "Use a professional, formal and trustworthy tone.",
    "casual": "Use a warm, friendly and relaxed conversational tone.",
    "technical": "Use industry-specific technical terms and professional jargon.",
    "luxury": "Use premium, sophisticated language. Make the client feel exclusive.",
}

_BUYER_SEGMENT_TR = {
    "investor": "yatırımcı",
    "holiday_home": "tatil evi alıcısı",
    "residence": "yaşam / oturum alıcısı",
    "generic": "genel alıcı",
}

_BUYER_SEGMENT_EN = {
    "investor": "investor",
    "holiday_home": "holiday-home buyer",
    "residence": "residence buyer",
    "generic": "general buyer",
}

_SEGMENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "investor": (
        "investment_and_rental_income",
        "i_want_to_invest_for_rental_income",
        "yatırım",
        "yatirim",
        "kira",
        "kiralama",
        "getiri",
        "roi",
        "yield",
        "airbnb",
        "rental",
        "rent",
    ),
    "holiday_home": (
        "holiday_home",
        "summer_house",
        "tatil evi",
        "yazlık",
        "yazlik",
        "tatil",
        "summer",
        "vacation",
        "ara ara kullan",
        "kendim de kullan",
        "yazın",
        "yazin",
    ),
    "residence": (
        "i_want_to_start_a_new_life_by_the_sea",
        "start a new life by the sea",
        "new life by the sea",
        "yaşamak",
        "yasamak",
        "kendim yaşamak",
        "kendim yasamak",
        "kendim yaşamak için",
        "kendim yasamak icin",
        "kendim oturmak",
        "oturum",
        "ikamet",
        "taşın",
        "tasin",
        "kendim için",
        "kendi evim",
        "aile",
        "çocuk",
        "cocuk",
        "okul",
        "işe yakın",
        "ise yakin",
        "daily life",
        "move in",
        "live in",
        "relocate",
    ),
}


_DEFAULT_PLAYBOOK_TR: dict[str, Any] = {
    "forbidden_topics": [
        "Rakip fiyat karşılaştırması veya rakip yorumları",
        "Siyasi ve hassas konular",
        "Hukuki yorum veya danışmanlık",
        "Vergi tavsiyesi",
        "Yatırım, sermaye kazancı veya kira getirisi garantisi",
        "Şirket içi gizli bilgiler ve spekülatif yorumlar",
    ],
    "faq_entries": [
        {
            "question": "Türk müşteri için kredi seçeneği var mı?",
            "answer": "Kredi uygunluğu ve modeli projeye ve müşteri profiline göre değişir; net seçenekler satış ekibiyle paylaşılır.",
        },
        {
            "question": "Yabancı alıcı için peşinat ve teslim çerçevesi nedir?",
            "answer": "Peşinat oranı ve teslim planı projeye göre değişir; net detay ilgili proje bazında paylaşılır.",
        },
        {
            "question": "Ödeme planları nasıl oluyor?",
            "answer": "Projeye göre peşinat + taksit veya teslimata kadar faizsiz taksit seçenekleri olabilir.",
        },
        {
            "question": "Airbnb veya kira getirisi garantisi var mı?",
            "answer": "Garanti veremeyiz; sadece bölge ve proje potansiyelini genel çerçevede paylaşabiliriz.",
        },
    ],
    "custom_qualifying_questions": [
        "Bu tarafı daha çok yatırım için mi, yaşam için mi, yoksa tatil evi gibi mi düşünüyorsunuz?",
        "Nakit, kredi veya taksitli plan tarafında nasıl bir çerçeve düşünüyorsunuz?",
        "Peşinat tarafı hazır mı; alımı daha çok ne zaman düşünüyorsunuz?",
        "Hangi lokasyon ve hangi mülk tipi size daha yakın duruyor?",
    ],
    "priority_target_segments": [
        "Yüksek bütçeli villa alıcısı",
        "İlk kez KKTC yatırımı yapacak yatırımcı",
        "Airbnb / kısa dönem gelir modeliyle ilgilenen yatırımcı",
        "Tatil evi arayan aile veya çift",
        "Hazır veya yakın teslim, sakin ve rafine yaşam arayan oturum alıcısı",
    ],
    "customer_types_to_avoid": [
        "Sadece fiyat veya piyasa araştırması yapan ama alım niyeti vermeyenler",
        "Rakip / sektör araştırması yapanlar",
        "Premium proje bekleyip bütçesi belirgin şekilde uyumsuz olanlar",
        "Karar yetkisi veya ihtiyaç çerçevesi hiç netleşmeyen ve sürekli oyalayanlar",
    ],
    "information_to_learn": [
        "Yatırım mı, yaşam mı, tatil evi mi",
        "Net bütçe veya bütçe aralığı",
        "Nakit mi, kredi mi, taksit mi",
        "Peşinat hazır mı",
        "Ne zaman almayı düşündüğü",
        "Hangi lokasyon ve mülk tipine yakın olduğu",
        "Kararı tek başına mı yoksa eşi / ailesiyle mi verdiği",
    ],
    "core_selling_points": [
        "Lokasyon gücü ve deniz / doğa hissi",
        "Proje kalitesi ve güven veren geliştirici algısı",
        "Airbnb ve uzun dönem kiralama için potansiyel, garanti vermeden",
        "Esnek ödeme planı ve proje bazlı finansman seçenekleri",
        "Hazır veya yaşama yakın ürünlerde sakin, rafine ve hafif Avrupa tarzı yaşam hissi",
    ],
    "prohibited_phrases": [
        "garantili kazanç",
        "kesin kira getirisi",
        "kesin teslim tarihi",
        "kesin sermaye kazancı",
        "rakiple doğrudan kıyas",
        "kesinlikle şu kadar kazanırsınız",
    ],
    "payment_guidance": [
        "Peşinat oranı, taksit süresi ve kredi modeli projeye göre değişir",
        "Türk müşteri kredi opsiyonunu sorarsa genel çerçeve ver, kesin onay veya oran verme",
        "Yabancı alıcıya peşinat ve teslim konusunu proje bazlı çerçevede anlat",
        "Bazen teslimata kadar faizsiz taksit olabilir; net planı doğrulamadan rakam verme",
    ],
    "lead_handoff_signals": [
        "Net bütçe veya bütçe aralığı paylaşıyorsa",
        "Arama, toplantı veya insan temsilci istiyorsa",
        "Belirli proje, ödeme planı, stok veya sözleşme detayına giriyorsa",
        "Yakın vadede alım düşündüğünü söylüyorsa",
        "Teklif, ödeme dağılımı veya sonraki adımı netleştirmek istiyorsa",
    ],
    "disengagement_guidelines": [
        "İlgisiz, kaba veya çok kısa cevaplı kullanıcıda baskıyı düşür",
        "Sadece araştırma modundaysa bilgi verip kapıyı açık bırak",
        "Konuşmayı tamamen kesmek yerine yumuşat, gerektiğinde nurture moduna dön",
    ],
    "brand_tone_notes": [
        "Sıcak ama profesyonel",
        "Samimi ama ölçülü",
        "Premium hissi olan fakat erişilebilir",
        "Yatırım ve güven odağını koruyan",
    ],
    "trust_building_phrases": [
        "Bu bölgede güçlü seçeneklerimiz var.",
        "Size uygun opsiyonlar çıkarabiliriz.",
        "İsterseniz adım adım ilerleyelim.",
        "Detayları netleştirip sizin için paylaşabilirim.",
    ],
    "additional_notes": [
        "İlk temas iyi olabilir ama derin yönlendirme gereken yerde insan satış temsilcisi devralmalı.",
        "Yatırım getirisi konuşulabilir, ama her zaman potansiyel diliyle ve garantiden uzak anlatılmalı.",
    ],
}

_DEFAULT_PLAYBOOK_EN: dict[str, Any] = {
    "forbidden_topics": [
        "Competitor price comparisons or competitor commentary",
        "Political or sensitive topics",
        "Legal interpretations or advice",
        "Tax advice",
        "Guaranteed investment, capital gain, or rental return claims",
        "Confidential company information or speculative commentary",
    ],
    "faq_entries": [
        {
            "question": "Is financing available for Turkish buyers?",
            "answer": "Financing availability depends on the project and buyer profile; exact options should be confirmed by sales.",
        },
        {
            "question": "What are the down payment and delivery terms for foreign buyers?",
            "answer": "Both down payment and delivery timing vary by project; share only high-level guidance unless verified.",
        },
        {
            "question": "How do payment plans usually work?",
            "answer": "Projects may offer a down payment plus installments, sometimes interest-free until completion.",
        },
        {
            "question": "Is Airbnb or rental income guaranteed?",
            "answer": "No guarantees should be given; only discuss general area and project potential.",
        },
    ],
    "custom_qualifying_questions": [
        "Are you looking more for investment, personal use, or a holiday home?",
        "Are you thinking cash, financing, or an installment plan?",
        "Is the down payment side already prepared, and what timeline are you considering?",
        "Which location and property type feel closest to what you want?",
    ],
    "priority_target_segments": [
        "High-budget villa buyers",
        "First-time Northern Cyprus investors",
        "Airbnb / short-term rental investors",
        "Families or couples looking for a holiday home",
        "Ready or near-ready residence buyers seeking a calm, refined lifestyle",
    ],
    "customer_types_to_avoid": [
        "People only price-shopping without buying intent",
        "Competitor or market-research inquiries",
        "Low-budget leads expecting premium inventory",
        "Leads who never clarify need or decision authority and keep stalling",
    ],
    "information_to_learn": [
        "Investment, residence, or holiday-home intent",
        "Budget or budget range",
        "Cash, financing, or installments",
        "Down payment readiness",
        "Purchase timeline",
        "Preferred location and property type",
        "Whether the decision is solo or shared",
    ],
    "core_selling_points": [
        "Location strength and sea / nature lifestyle",
        "Project quality and trust",
        "Airbnb and long-term rental potential without guarantees",
        "Flexible payment plans and project-based financing options",
        "Calm, refined, lightly European lifestyle appeal for ready-to-live projects",
    ],
    "prohibited_phrases": [
        "guaranteed profit",
        "fixed rental income",
        "guaranteed delivery date",
        "guaranteed capital gain",
        "direct competitor comparison",
        "you will definitely make X",
    ],
    "payment_guidance": [
        "Down payment, installment length, and financing vary by project",
        "For Turkish buyers, keep financing language high-level unless verified",
        "For foreign buyers, frame down payment and delivery timing as project-specific",
        "Interest-free installments may exist on some projects; do not invent exact terms",
    ],
    "lead_handoff_signals": [
        "They share a clear budget or range",
        "They request a call, meeting, or human representative",
        "They ask about a specific project, payment plan, stock, or contract detail",
        "They indicate near-term purchase intent",
        "They want a concrete quote, payment breakdown, or next step",
    ],
    "disengagement_guidelines": [
        "Reduce pressure if the user is uninterested, rude, or giving very short replies",
        "If they are still researching, stay helpful and leave the door open",
        "Soften rather than abruptly ending the conversation when possible",
    ],
    "brand_tone_notes": [
        "Warm yet professional",
        "Friendly but measured",
        "Premium but approachable",
        "Trust and investment aware",
    ],
    "trust_building_phrases": [
        "We have strong options in this area.",
        "We can narrow down options that suit you.",
        "We can move step by step if you like.",
        "I can confirm the details and share the clearest options for you.",
    ],
    "additional_notes": [
        "The chatbot should handle discovery well, but deeper emotional guidance should transition to a human sales advisor.",
        "Investment return can be discussed only as potential, never as a guarantee.",
    ],
}


def _is_turkish(language: str) -> bool:
    return language.lower().strip() in ("tr", "turkish")


def _resolve_language(cfg: dict[str, Any], language: str | None = None) -> str:
    return (language or cfg.get("primary_language") or "tr").lower().strip()


def _resolve_sector(cfg: dict[str, Any], sector: str | None = None) -> str:
    return sector or cfg.get("industry_focus") or "construction"


def _get_form_value(form: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = form.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _normalize_match_text(value: str) -> str:
    return " ".join(value.lower().split())


def _normalize_ascii_match_text(value: str) -> str:
    collapsed = " ".join(str(value or "").lower().replace("ı", "i").split())
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", collapsed)
        if not unicodedata.combining(character)
    )


def _collect_recent_user_text(messages: list[dict[str, Any]] | None, limit: int = 4) -> str:
    if not messages:
        return ""
    user_messages = [
        str(message.get("content", "")).strip()
        for message in messages
        if str(message.get("role", "")).lower() == "user" and message.get("content")
    ]
    return " ".join(user_messages[-limit:])


def _lead_has_contact_details(lead_json: dict[str, Any]) -> bool:
    form_data = lead_json.get("form_data")
    form = form_data if isinstance(form_data, dict) else {}
    contact = lead_json.get("contact")
    contact_obj = contact if isinstance(contact, dict) else {}

    keys = (
        "email",
        "mail",
        "phone",
        "mobile",
        "whatsapp",
        "contact_email",
        "contact_phone",
    )
    for key in keys:
        value = contact_obj.get(key)
        if value not in (None, ""):
            return True
        value = form.get(key)
        if value not in (None, ""):
            return True
    return False


def _localize_buyer_segment(segment: str, language: str) -> str:
    mapping = _BUYER_SEGMENT_TR if _is_turkish(language) else _BUYER_SEGMENT_EN
    return mapping.get(segment, mapping["generic"])


def _normalize_buyer_segment_hint(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""

    normalized = normalized.replace("-", "_").replace(" ", "_")
    mapping = {
        "investor": "investor",
        "yatirimci": "investor",
        "yatırımcı": "investor",
        "holiday_home": "holiday_home",
        "holidayhome": "holiday_home",
        "holiday_home_buyer": "holiday_home",
        "tatil_evi": "holiday_home",
        "tatil_evi_alicisi": "holiday_home",
        "residence": "residence",
        "oturum": "residence",
        "yasam": "residence",
        "yaşam": "residence",
        "residence_buyer": "residence",
        "generic": "generic",
        "general": "generic",
    }
    return mapping.get(normalized, "")


def _apply_buyer_segment_hint(
    lead_json: dict[str, Any],
    buyer_segment_hint: Any,
) -> dict[str, Any]:
    normalized = _normalize_buyer_segment_hint(buyer_segment_hint)
    if not normalized:
        return lead_json
    if _normalize_buyer_segment_hint(lead_json.get("buyer_segment")):
        return lead_json
    return {**lead_json, "buyer_segment": normalized}


def _resolve_buyer_segment(
    lead_json: dict[str, Any],
    messages: list[dict[str, Any]] | None = None,
    language: str = "tr",
) -> str:
    form_data = lead_json.get("form_data")
    form = form_data if isinstance(form_data, dict) else {}

    explicit_segment = _normalize_buyer_segment_hint(
        lead_json.get("buyer_segment")
        or lead_json.get("segment")
        or _get_form_value(form, "buyer_segment", "customer_segment")
    )
    if explicit_segment:
        return explicit_segment

    texts = [
        str(lead_json.get("notes") or ""),
        str(lead_json.get("project_type") or ""),
        _get_form_value(
            form,
            "why_are_you_interested_in_north_cyprus?",
            "why_are_you_interested_in_north_cyprus",
            "purpose",
        ),
        _get_form_value(
            form,
            "what_type_of_property_are_you_interested_in?",
            "what_type_of_property_are_you_interested_in",
            "project_type",
        ),
        _collect_recent_user_text(messages),
    ]

    scores = {"investor": 0, "holiday_home": 0, "residence": 0}
    weighted_texts = [
        (_normalize_match_text(texts[0]), 2),
        (_normalize_match_text(texts[1]), 1),
        (_normalize_match_text(texts[2]), 4),
        (_normalize_match_text(texts[3]), 1),
        (_normalize_match_text(texts[4]), 2),
    ]

    for text, weight in weighted_texts:
        if not text:
            continue
        for segment, keywords in _SEGMENT_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                scores[segment] += weight

    if scores["residence"] and scores["holiday_home"] and scores["residence"] >= scores["holiday_home"] + 2:
        return "residence"
    if scores["investor"] and scores["investor"] >= scores["holiday_home"] + 2:
        return "investor"
    if scores["holiday_home"]:
        return "holiday_home"
    if scores["investor"]:
        return "investor"
    if scores["residence"]:
        return "residence"
    return "generic"


def resolve_buyer_segment(
    lead_json: dict[str, Any],
    messages: list[dict[str, Any]] | None = None,
    language: str = "tr",
) -> str:
    return _resolve_buyer_segment(lead_json, messages, language)


def _build_buyer_segment_section(
    buyer_segment: str,
    language: str,
) -> str:
    if not _is_turkish(language):
        return ""

    label = _localize_buyer_segment(buyer_segment, language)
    if buyer_segment == "investor":
        return (
            "[AKTİF MÜŞTERİ TİPİ]\n"
            f"- Bu lead şu anda en çok {label} gibi görünüyor; bunu lead formu + sohbetten gelen güçlü bir ön bilgi olarak kabul et.\n"
            "- İlk turlarda segmenti yeniden keşfetmeye çalışma. Öncelik yatırım modelini netleştirmek olsun.\n"
            "- Özellikle şunu anlamaya çalış: projeyi erken alıp teslime yakın satmak mı, uzun dönem kiraya vermek mi, Airbnb / kısa dönem değerlendirmek mi, yoksa hibrit bir plan mı var?\n"
            "- Cyprus Constructions tarafında Airbnb yönetimi konuşulabilir; exact oran, sözleşme veya garanti dili doğrulanmadan söyleme.\n"
            "- Doğrulanmamış ROI, kira oranı veya garanti kazanç dili kullanma.\n"
        )
    if buyer_segment == "holiday_home":
        return (
            "[AKTİF MÜŞTERİ TİPİ]\n"
            f"- Bu lead şu anda en çok {label} gibi görünüyor; bunu lead formu + sohbetten gelen güçlü bir ön bilgi olarak kabul et.\n"
            "- Öncelik: Kıbrıs'a hakimiyeti, kullanım dönemi, erişim rahatlığı, bakım/uzaktan kullanım ve satın alma zamanı.\n"
            "- Deniz / merkez tercihi yardımcı sinyaldir; proje adına geçmeden önce kullanım şekli ve zamanlamayı biraz netleştir.\n"
            "- Dilin daha çok konfor, kolaylık, huzur ve kullanım rahatlığı tarafında olsun.\n"
        )
    if buyer_segment == "residence":
        return (
            "[AKTİF MÜŞTERİ TİPİ]\n"
            f"- Bu lead şu anda en çok {label} gibi görünüyor; bunu lead formu + sohbetten gelen güçlü bir ön bilgi olarak kabul et.\n"
            "- Öncelik: taşınma tarihi, günlük yaşam ihtiyaçları, mahalle / ulaşım, aile karar süreci ve finansman hazırlığı.\n"
            "- İlk turlarda yatırım diliyle değil yaşam uyumu, rutin ve ihtiyaç netliğiyle ilerle.\n"
        )
    return (
        "[AKTİF MÜŞTERİ TİPİ]\n"
        "- Segment henüz net değil.\n"
        "- Önce amaç, kullanım şekli ve zamanlamayı netleştir; sonra detaylara geç.\n"
    )


def _build_judge_buyer_segment_section(buyer_segment: str, language: str) -> str:
    if _is_turkish(language):
        label = _localize_buyer_segment(buyer_segment, language)
        if buyer_segment == "investor":
            return (
                "## Tahmini Müşteri Tipi\n"
                f"- Bu lead büyük ihtimalle {label}; lead formu veya sohbet bunu zaten güçlü gösteriyorsa bunu yeniden ispat etmeye çalışma.\n"
                "- Düşünürken yatırımın hangi modelde kurgulandığını özellikle ayır: erken alıp teslimde satmak, uzun dönem kira, Airbnb / kısa dönem kullanım veya hibrit.\n"
                "- Cyprus Constructions tarafında Airbnb yönetimi konuşulabilir; ama oran, komisyon, garanti gelir veya sözleşme şartlarını doğrulanmış kabul etme.\n"
                "- Finansman hazırlığı, bütçe netliği ve zamanlamayı özellikle dikkate al.\n"
            )
        if buyer_segment == "holiday_home":
            return (
                "## Tahmini Müşteri Tipi\n"
                f"- Bu lead büyük ihtimalle {label}; lead formu veya sohbet bunu zaten güçlü gösteriyorsa aynı segment sorusuna geri dönme.\n"
                "- Düşünürken kullanım dönemi, Kıbrıs bilgisi, erişim rahatlığı, bakım kolaylığı ve satın alma zamanını özellikle dikkate al.\n"
            )
        if buyer_segment == "residence":
            return (
                "## Tahmini Müşteri Tipi\n"
                f"- Bu lead büyük ihtimalle {label}; lead formu veya sohbet bunu zaten güçlü gösteriyorsa aynı segment sorusuna geri dönme.\n"
                "- Düşünürken taşınma tarihi, günlük yaşam ihtiyaçları, mahalle / ulaşım ve aile karar sürecini özellikle dikkate al.\n"
            )
        return (
            "## Tahmini Müşteri Tipi\n"
            "- Segment henüz net değil; handoff öncesi bunu netleştirmeyi öncelik kabul et.\n"
        )

    return (
        "## Estimated Buyer Segment\n"
        f"- Likely segment: {_localize_buyer_segment(buyer_segment, language)}.\n"
    )


def _localize_sector_name(sector: str, language: str) -> str:
    normalized = sector.lower().strip()
    if _is_turkish(language):
        return {
            "construction": "inşaat ve gayrimenkul",
            "real estate": "gayrimenkul",
            "real_estate": "gayrimenkul",
        }.get(normalized, sector)
    return {
        "insaat": "construction",
        "inşaat": "construction",
        "gayrimenkul": "real estate",
    }.get(normalized, sector)


def _playbook_defaults(language: str) -> dict[str, Any]:
    return _DEFAULT_PLAYBOOK_TR if _is_turkish(language) else _DEFAULT_PLAYBOOK_EN


def _resolve_playbook_list(cfg: dict[str, Any], key: str, language: str) -> list[str]:
    value = cfg.get(key)
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if cleaned:
            return cleaned
    default_value = _playbook_defaults(language).get(key, [])
    if isinstance(default_value, list):
        return [str(item).strip() for item in default_value if str(item).strip()]
    return []


def _resolve_playbook_faq(cfg: dict[str, Any], language: str) -> list[dict[str, str]]:
    value = cfg.get("faq_entries")
    if isinstance(value, list):
        cleaned: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question", "")).strip()
            answer = str(item.get("answer", "")).strip()
            if question and answer:
                cleaned.append({"question": question, "answer": answer})
        if cleaned:
            return cleaned
    default_value = _playbook_defaults(language).get("faq_entries", [])
    if isinstance(default_value, list):
        return [
            {"question": str(item.get("question", "")).strip(), "answer": str(item.get("answer", "")).strip()}
            for item in default_value
            if isinstance(item, dict)
            and str(item.get("question", "")).strip()
            and str(item.get("answer", "")).strip()
        ]
    return []


def _resolve_ideal_customer_profile(
    cfg: dict[str, Any],
    language: str,
    fallback: str,
) -> str:
    value = str(cfg.get("ideal_customer_profile") or "").strip()
    if value:
        return value
    playbook_value = str(_playbook_defaults(language).get("ideal_customer_profile") or "").strip()
    return playbook_value or fallback


def _format_playbook_section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    formatted = "\n".join(f"- {item}" for item in items)
    return f"[{title}]\n{formatted}\n"


def _build_brand_voice_section(
    cfg: dict[str, Any],
    language: str,
    tone_instruction: str,
) -> str:
    brand_tone = _resolve_playbook_list(cfg, "brand_tone_notes", language)
    trust_phrases = _resolve_playbook_list(cfg, "trust_building_phrases", language)
    additional_notes = _resolve_playbook_list(cfg, "additional_notes", language)

    if _is_turkish(language):
        parts = [
            "[MARKA TONU VE GUVEN]",
            f"- Ton cekirdegi: {tone_instruction}",
        ]
        if brand_tone:
            parts.append(f"- Marka tonu: {', '.join(brand_tone)}")
        if trust_phrases:
            parts.append(f"- Guven verirken su tip kisa cumleleri tercih et: {' | '.join(trust_phrases)}")
        if additional_notes:
            parts.extend(f"- {note}" for note in additional_notes)
        return "\n" + "\n".join(parts) + "\n"

    parts = [
        "[BRAND VOICE AND TRUST]",
        f"- Core tone: {tone_instruction}",
    ]
    if brand_tone:
        parts.append(f"- Brand notes: {', '.join(brand_tone)}")
    if trust_phrases:
        parts.append(f"- Prefer short reassurance lines such as: {' | '.join(trust_phrases)}")
    if additional_notes:
        parts.extend(f"- {note}" for note in additional_notes)
    return "\n" + "\n".join(parts) + "\n"


def _build_chat_sales_playbook_section(
    cfg: dict[str, Any],
    language: str,
) -> str:
    priority_segments = _resolve_playbook_list(cfg, "priority_target_segments", language)
    avoid_types = _resolve_playbook_list(cfg, "customer_types_to_avoid", language)
    learn_items = _resolve_playbook_list(cfg, "information_to_learn", language)
    selling_points = _resolve_playbook_list(cfg, "core_selling_points", language)
    payment_guidance = _resolve_playbook_list(cfg, "payment_guidance", language)
    handoff_signals = _resolve_playbook_list(cfg, "lead_handoff_signals", language)
    disengagement = _resolve_playbook_list(cfg, "disengagement_guidelines", language)
    prohibited_phrases = _resolve_playbook_list(cfg, "prohibited_phrases", language)

    if _is_turkish(language):
        parts = [
            _format_playbook_section("ONCELIKLI HEDEF SEGMENTLER", priority_segments),
            _format_playbook_section("KACINILACAK LEAD TIPLERI", avoid_types),
            _format_playbook_section("MUSTERIDEN DOGAL SEKILDE OGRENMEN GEREKENLER", learn_items),
            _format_playbook_section("ONE CIKARILACAK SATIS NOKTALARI", selling_points),
            _format_playbook_section("ODEME VE FINANS CERCEVESI", payment_guidance),
            _format_playbook_section("SATISA YONLENDIRME SINYALLERI", handoff_signals),
            _format_playbook_section("YUMUSATMA / GERI CEKILME KURALLARI", disengagement),
            _format_playbook_section("ASLA KULLANMAMAN GEREKEN IFADELER", prohibited_phrases),
        ]
        return "\n" + "".join(part for part in parts if part)

    parts = [
        _format_playbook_section("PRIORITY TARGET SEGMENTS", priority_segments),
        _format_playbook_section("LEAD TYPES TO AVOID", avoid_types),
        _format_playbook_section("INFORMATION TO LEARN NATURALLY", learn_items),
        _format_playbook_section("KEY SELLING POINTS", selling_points),
        _format_playbook_section("PAYMENT AND FINANCE GUIDANCE", payment_guidance),
        _format_playbook_section("LEAD HANDOFF SIGNALS", handoff_signals),
        _format_playbook_section("DISENGAGEMENT GUIDELINES", disengagement),
        _format_playbook_section("PROHIBITED PHRASES", prohibited_phrases),
    ]
    return "\n" + "".join(part for part in parts if part)


def _build_judge_sales_playbook_section(
    cfg: dict[str, Any],
    language: str,
) -> str:
    priority_segments = _resolve_playbook_list(cfg, "priority_target_segments", language)
    avoid_types = _resolve_playbook_list(cfg, "customer_types_to_avoid", language)
    handoff_signals = _resolve_playbook_list(cfg, "lead_handoff_signals", language)
    payment_guidance = _resolve_playbook_list(cfg, "payment_guidance", language)

    if _is_turkish(language):
        sections = [
            _format_playbook_section("SATIS PLAYBOOK'U - ONCELIKLI SEGMENTLER", priority_segments),
            _format_playbook_section("SATIS PLAYBOOK'U - DUSUK KALITE LEADLER", avoid_types),
            _format_playbook_section("SATIS PLAYBOOK'U - HANDOFF SINYALLERI", handoff_signals),
            _format_playbook_section("SATIS PLAYBOOK'U - ODEME CERCEVESI", payment_guidance),
        ]
        return "\n" + "".join(section for section in sections if section)

    sections = [
        _format_playbook_section("SALES PLAYBOOK - PRIORITY SEGMENTS", priority_segments),
        _format_playbook_section("SALES PLAYBOOK - LOW QUALITY LEADS", avoid_types),
        _format_playbook_section("SALES PLAYBOOK - HANDOFF SIGNALS", handoff_signals),
        _format_playbook_section("SALES PLAYBOOK - PAYMENT GUIDANCE", payment_guidance),
    ]
    return "\n" + "".join(section for section in sections if section)


def _build_knowledge_guard_section(language: str, kb_content: str) -> str:
    has_verified_knowledge = bool(kb_content.strip())
    if _is_turkish(language):
        if has_verified_knowledge:
            return (
                "\n[BİLGİ KORUMA KURALI]\n"
                "Aşağıdaki KB/RAG şirket içi doğrulanmış veri kabul edilir.\n"
                "Proje adı, şehir, bölge, fiyat, facility, teslim tarihi, ödeme planı, stok veya karşılaştırma söyleyeceksen SADECE bu veriye dayan.\n"
                "Bu veride olmayan hiçbir proje detayı uydurma.\n"
            )
        return (
            "\n[BİLGİ KORUMA KURALI]\n"
            "Şu anda doğrulanmış KB/RAG proje verisi yok.\n"
            "Bu yüzden net proje adı, lokasyon, fiyat, metrekare, stok, teslim tarihi veya proje karşılaştırması verme.\n"
            "Genel konuş, gerekiyorsa 'net örnekleri kontrol edip döneyim' de.\n"
        )

    if has_verified_knowledge:
        return (
            "\n[KNOWLEDGE GUARD]\n"
            "Treat the KB/RAG section below as the only verified project data.\n"
            "If you mention project names, locations, prices, facilities, delivery dates, payment plans, stock, or comparisons, use ONLY that verified data.\n"
            "Do not invent any project detail outside it.\n"
        )
    return (
        "\n[KNOWLEDGE GUARD]\n"
        "There is no verified KB/RAG project data loaded right now.\n"
        "Do not mention exact project names, locations, prices, square meters, stock, delivery dates, or project comparisons.\n"
        "Stay general and say you can confirm the exact examples if needed.\n"
    )


def _resolve_assistant_name(cfg: dict[str, Any], language: str) -> str:
    value = (
        cfg.get("assistant_name")
        or cfg.get("agent_name")
        or cfg.get("sales_agent_name")
        or ""
    )
    if value:
        return str(value).strip()
    return "Gözde" if _is_turkish(language) else "Sarah"


def _humanize_lead_value(value: str, language: str) -> str:
    if not value:
        return ""

    normalized = value.strip().lower()
    tr_map = {
        "investment_and_rental_income": "yatırım ve kira getirisi",
        "i_want_to_invest_for_rental_income": "yatırım ve kira getirisi",
        "i'm_looking_for_a_holiday_home_/_summer_house": "tatil evi / yazlık",
        "i_want_to_start_a_new_life_by_the_sea": "kendisi yaşamak / taşınmak için",
        "holiday_home": "tatil evi",
        "4+1_detached_villa_with_pool": "4+1 havuzlu müstakil villa",
        "3+1_villa_with_private_pool": "3+1 özel havuzlu villa",
        "3+1_or_4+1_private_villa": "3+1 veya 4+1 özel villa",
        "2+1_penthouse": "2+1 penthouse",
        "studio_apartment": "stüdyo daire",
        "residential": "konut",
        "commercial": "ticari",
        "renovation": "tadilat",
        "land": "arsa",
    }
    en_map = {
        "investment_and_rental_income": "investment and rental income",
        "i_want_to_invest_for_rental_income": "investment and rental income",
        "i_want_to_start_a_new_life_by_the_sea": "for living / relocating",
        "4+1_detached_villa_with_pool": "4+1 detached villa with pool",
        "3+1_villa_with_private_pool": "3+1 villa with private pool",
        "3+1_or_4+1_private_villa": "3+1 or 4+1 private villa",
        "2+1_penthouse": "2+1 penthouse",
    }
    mapping = tr_map if _is_turkish(language) else en_map
    if normalized in mapping:
        return mapping[normalized]

    cleaned = (
        value.replace("_/_", " / ")
        .replace("_-_", " - ")
        .replace("_", " ")
        .strip()
    )
    return " ".join(cleaned.split())


def _build_prompt_lead_context(lead_json: dict[str, Any], language: str) -> dict[str, Any]:
    form_data = lead_json.get("form_data")
    form = form_data if isinstance(form_data, dict) else {}
    contact = lead_json.get("contact")
    contact_obj = contact if isinstance(contact, dict) else {}
    buyer_segment = _resolve_buyer_segment(lead_json, None, language)

    project_interest_raw = _humanize_lead_value(
        _get_form_value(
            form,
            "what_type_of_property_are_you_interested_in?",
            "what_type_of_property_are_you_interested_in",
            "project_type",
        )
        or str(lead_json.get("project_type") or ""),
        language,
    )
    # Simplify to general category for soft reference (prevent AI from copying "4+1 havuzlu müstakil villa")
    _interest_lower = project_interest_raw.lower()
    if "villa" in _interest_lower:
        project_interest = "villa"
    elif "penthouse" in _interest_lower:
        project_interest = "penthouse"
    elif "stüdyo" in _interest_lower or "studio" in _interest_lower:
        project_interest = "stüdyo daire"
    elif "daire" in _interest_lower or "apartment" in _interest_lower:
        project_interest = "daire"
    elif "arsa" in _interest_lower or "land" in _interest_lower:
        project_interest = "arsa"
    else:
        project_interest = project_interest_raw
    budget_signal = _humanize_lead_value(
        _get_form_value(
            form,
            "what_is_your_budget_range?",
            "what_is_your_budget_range",
            "budget_range",
        )
        or str(lead_json.get("budget_range") or ""),
        language,
    )
    purpose_signal = _humanize_lead_value(
        _get_form_value(
            form,
            "why_are_you_interested_in_north_cyprus?",
            "why_are_you_interested_in_north_cyprus",
            "purpose",
        )
        or str(lead_json.get("notes") or ""),
        language,
    )
    preferred_location = _humanize_lead_value(
        _get_form_value(form, "preferred_location", "city") or str(lead_json.get("city") or ""),
        language,
    )
    contact_pref = _humanize_lead_value(
        _get_form_value(
            form,
            "how_would_you_prefer_us_to_contact_you?",
            "how_would_you_prefer_us_to_contact_you",
            "contact_preference",
        ),
        language,
    )

    context = {
        "name": lead_json.get("name") or lead_json.get("full_name") or contact_obj.get("name") or "",
        "source": lead_json.get("source") or "",
        "buyer_segment": buyer_segment,
        "project_interest": project_interest,
        "budget_signal": budget_signal,
        "purpose_signal": purpose_signal,
        "preferred_location_signal": preferred_location,
        "contact_preference": contact_pref,
        "notes": lead_json.get("notes") or "",
    }

    raw_project_type = str(lead_json.get("project_type") or "").strip()
    if raw_project_type and raw_project_type != context.get("project_interest"):
        context["project_type_raw"] = raw_project_type

    return {key: value for key, value in context.items() if value not in ("", None)}


def _build_turn_guidance_section(
    messages: list[dict[str, Any]] | None,
    language: str,
    lead_json: dict[str, Any] | None = None,
    champ_json: dict[str, Any] | None = None,
) -> str:
    lead_facts = _extract_lead_facts(lead_json or {}, language) if lead_json else {}
    has_form_purpose = bool(lead_facts.get("purpose"))
    has_form_budget = bool(lead_facts.get("budget"))
    has_form_location = bool(lead_facts.get("location"))
    has_contact_details = _lead_has_contact_details(lead_json or {})
    buyer_segment = _resolve_buyer_segment(lead_json or {}, messages, language)

    if not messages:
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Bu ilk mesaj. Kendini tanıt, şirket ismini söyle, formdan tek bir detayı doğalca an ve sadece hal hatır sor.\n"
                "İlk mesajda bütçe sorma, lokasyon seçtirme, fiyat dökme veya proje anlatma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "This is the first message. Introduce yourself, mention the company, reference one form detail naturally, and only ask how they are.\n"
            "Do not ask for budget, location preference, or give project details in the first turn.\n"
        )

    last_user = ""
    for message in reversed(messages):
        role = str(message.get("role", "")).lower()
        if role == "user":
            last_user = str(message.get("content", "")).strip()
            break

    if not last_user:
        return ""

    normalized = " ".join(last_user.lower().split())

    # Collect ALL consecutive user messages since last assistant reply
    # (handles debounce=0 where user sends multiple rapid messages)
    combined_user_burst = last_user
    for message in reversed(messages[:-1]):
        role = str(message.get("role", "")).lower()
        if role == "assistant":
            break
        if role == "user" and message.get("content"):
            combined_user_burst = str(message["content"]).strip() + " " + combined_user_burst
    normalized_burst = " ".join(combined_user_burst.lower().split())

    previous_assistant = ""
    for message in reversed(messages):
        role = str(message.get("role", "")).lower()
        if role == "assistant":
            previous_assistant = str(message.get("content", "")).strip().lower()
            break

    normalized_burst_ascii = _normalize_ascii_match_text(combined_user_burst)
    previous_assistant_ascii = _normalize_ascii_match_text(previous_assistant)

    purpose_already_signaled = has_form_purpose or any(
        token in previous_assistant
        for token in (
            "yatırım için",
            "yatirim icin",
            "yatırım amaçlı",
            "yatirim amacli",
            "tatil evi",
            "holiday home",
            "yazlık",
            "yazlik",
            "oturum",
            "yaşamak",
            "yasamak",
        )
    )

    if has_form_budget and any(
        token in normalized_burst_ascii
        for token in ("butcem ne kadardi", "butcem kacti", "butcem neydi", "butcemi hatirlat")
    ):
        if _is_turkish(language):
            extra_payment = ""
            if any(token in normalized_burst_ascii for token in ("pesinat", "odeme", "taksit", "kredi", "finansman")):
                extra_payment = (
                    " Kullanıcı aynı turda peşinat/ödeme de soruyorsa ikisini de cevapla: bütçeyi formdan kısa söyle, "
                    "peşinat için ise sadece genel çerçeve ver.\n"
                    "Net yüzde, 'genelde %20-30' gibi yuvarlak oran veya vade UYDURMA; "
                    "'projeye göre değişiyor, net oranı proje bazında paylaşabilirim' çizgisinde kal.\n"
                )
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı formdaki bütçe bilgisini hatırlatmanı istiyor.\n"
                "Önce formdaki net bütçe sinyalini kısa ve doğrudan söyle. Aynı bilgiyi yeniden sorma.\n"
                f"{extra_payment}"
                "Bu turda yeni qualification sorusu sorma; önce sorulan bilgiyi tamamla.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking you to remind them of the budget already visible in the form.\n"
            "State that budget briefly first and do not ask for it again.\n"
        )

    if (
        any(token in normalized_burst_ascii for token in ("olur", "olut", "isterim", "tamam", "gonder", "paylas"))
        and any(
            token in previous_assistant_ascii
            for token in (
                "teklif",
                "ornek",
                "odeme plani",
                "odeme",
                "hazirlayayim",
                "hazirlayip",
                "paylas",
                "ileteyim",
                "ilet",
                "gondereyim",
            )
        )
    ):
        if _is_turkish(language):
            existing_contact_line = (
                "İletişim kanalı lead/form içinde görünüyorsa e-posta veya telefonu yeniden isteme; mevcut kanaldan paylaşabileceğini söyle.\n"
                if has_contact_details
                else "İletişim kanalı görünmüyorsa sadece hangi kanaldan paylaşmamı istersiniz diye sor; aynı onayı tekrar isteme.\n"
            )
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı teklif veya paylaşım için onay verdi.\n"
                "Bunu satın alma kararı gibi okuma; bu sadece devam etmek için izin. Aynı 'olur mu / ister misiniz' onayını tekrar isteme.\n"
                f"{existing_contact_line}"
                "Doğrulanmamış fiyat kombinasyonu, taksit vadesi veya özel teklif detayı UYDURMA.\n"
                "Bu onay yazım hatalı bile gelse ('olut' gibi) izin olarak yorumla; tekrar peşinat oranı veya başka onay isteme.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user has already consented to receive the quote or information.\n"
            "Treat that as permission to proceed, not as a purchase commitment, and do not ask for the same consent again.\n"
        )

    # "evet?" = user wants you to continue / tell me more (NOT a greeting)
    if normalized_burst in {"evet", "evet?", "evet??"}:
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı 'evet?' diyerek devam etmeni bekliyor.\n"
                "'Nasıl yardımcı olabilirim?' DEME. Doğal şekilde devam et.\n"
                "Kıbrıs araştırıp araştırmadığını sor veya ne tür bir şey düşündüğünü sor.\n"
                "İYİ: 'Kıbrıs yatırım tarafını biraz araştırma fırsatınız oldu mu?'\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "User said 'yes?' expecting you to continue. Do NOT say 'how can I help'. Ask a natural discovery question.\n"
        )

    # "yani???", "eee", "ee", "devam", "peki sonra" = user wants you to GET TO THE POINT / advance
    if normalized_burst in {"eee", "ee", "e", "eee?", "ee?", "devam", "devam et", "peki", "peki sonra", "sonra", "ve", "yani", "yani?", "yani??", "yani???", "??"} or any(
        token in normalized_burst for token in ("eee", "devam et", "anlat", "söylesene", "soylesene")
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN — BU TALİMAT DİĞER TÜM KURALLARI GEÇERSİZ KILAR]\n"
                "Kullanıcı senden daha somut bilgi bekliyor veya konuşmayı ilerletmeni istiyor.\n"
                "Muhtemelen bir sorunun bir kısmı eksik kaldı ya da fazla havada konuştun. Orient/tanıtım modunda takılma. Bir adım ilerle:\n"
                "- Eğer henüz ne istediği belli değilse: 'Stüdyo dairelerden villaya kadar farklı seçeneklerimiz var. Nasıl bir şey ilginizi çeker?' gibi somut bir soru sor.\n"
                "- Eğer istediği belli ise: eksik kalan cevabı tamamla veya KB'den uygun bir proje ile kısa bilgi ver.\n"
                "Aynı bölge tanıtımını TEKRARLAMA. Soyut konuşma, somut ilerle. Gerekirse çok kısa özür + eksik bilgi.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user wants you to get to the point. Advance the conversation with concrete info or a specific question.\n"
        )

    if any(token in normalized_burst for token in ("nasıl yani", "nasil yani", "bilgim yok dedim", "bilgim yok demiştim", "bilmiyorum dedim")) and any(
        token in previous_assistant
        for token in ("kıbrıs", "kibris", "bölge", "bolge", "esentepe", "girne", "çatalköy", "catalkoy")
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı seni düzeltiyor: Kıbrıs'ı bilmediğini söylemişti ve sen onu erken bir bölge seçimine ittirdin.\n"
                "Önce bunu kısa şekilde kabul et ve cümleyi daha basit kur. 'Hangi bölgeyi tercih edersiniz' veya benzeri bir lokasyon seçimi SORMA.\n"
                "Kısa ve sade bir çerçeve ver: sahil tarafı daha sakin, merkeze yakın taraf daha hareketli olabilir demek yeterli.\n"
                "İstersen en fazla 1 çok kolay sonraki adım sorusu sor; en güvenli soru zamanlama veya kullanım amacıdır. Aynı mesajda yeni lokasyon sorusu ekleme.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is correcting you because they already said they do not know the area.\n"
            "Acknowledge that, simplify your wording, and do not force a location choice in this turn.\n"
        )

    if any(
        token in normalized
        for token in ("anlamadım", "anlamadim", "ne demek", "derken neyi", "neyi kastettin", "nasıl olur", "nasil olur", "ne belgesi", "hangi belge", "belge ne", "belge mi")
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı son cümleni anlamadı.\n"
                "Aynı şeyi daha basit ve somut tek cümleyle açıkla. Yeni soru ekleme.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user did not understand your last message.\n"
            "Restate in simpler terms. Do not add a new question.\n"
        )

    if any(
        token in normalized_burst
        for token in ("söylemiştim", "soylemistim", "demiştim", "demistim", "zaten dedim", "az önce söyledim", "az once soyledim")
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı bu bilgiyi daha önce söylediğini hatırlatıyor.\n"
                "Savunmaya geçme. Kısa şekilde hak ver, düzeltilen bilgiyi baz al ve aynı şeyi tekrar sorma.\n"
                "Bu turda 'daha önceki görüşmelerde söylemiştiniz' gibi görünmeyen bir geçmiş UYDURMA.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is reminding you they already said this.\n"
            "Acknowledge it briefly, use the corrected fact, and do not defend yourself or invent prior history.\n"
        )

    if any(
        token in normalized_burst
        for token in (
            "kendim yaşamayı düşünüyorum",
            "kendim yasamayi dusunuyorum",
            "kendim yaşamayı",
            "kendim yasamayi",
            "kendim yaşayacağım",
            "kendim yasayacagim",
            "tamamen yaşayacağım",
            "tamamen yasayacagim",
            "oturacağım",
            "oturacagim",
            "yaşayabileceğim",
            "yasayabilecegim",
            "uzaktan çalışıyorum",
            "uzaktan calisiyorum",
            "uzaktan yapıyorum",
            "uzaktan yapiyorum",
            "uzaktan",
            "remote çalışıyorum",
            "remote calisiyorum",
        )
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı bunun kendi yaşamı için olduğunu ve günlük hayat / uzaktan çalışma açısından değerlendirdiğini netleştirdi.\n"
                "Bunu not al. Bu turdan sonra mevsimsel kullanım, Airbnb modeli veya yatırım senaryosu sorma.\n"
                "Bir sonraki doğal adım tek somut yaşam filtresidir: ör. 3+1 mi 4+1 mi, denize yakınlık mı merkez erişimi mi, müstakil mi site içi mi.\n"
                "Soyut 'hangi kriterler önemli' dili kullanma; daha insan gibi, somut ve kısa sor.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user clarified that this is for their own living situation and remote-work lifestyle.\n"
            "Do not pivot into seasonal use, Airbnb, or investor questions. The next step should be one concrete lifestyle filter only.\n"
        )

    if any(
        token in normalized_burst
        for token in ("nereden aldın", "nereden aldin", "bunu nereden çıkardın", "bunu nereden cikardin", "eşim olduğu bilgisini", "esim oldugu bilgisini")
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı senin yanlış bir varsayım yaptığını fark etti.\n"
                "Kısa ve net şekilde hatanı kabul et. Görünmeyen eski konuşma, CRM notu veya önceki görüşme UYDURMA.\n"
                "Doğru bilgiyi baz alarak devam et ve bu turda yeni baskı kurma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is challenging a wrong assumption you made.\n"
            "Admit the mistake briefly. Do not invent previous conversations or hidden CRM notes.\n"
        )

    if any(token in normalized for token in ("hangi şirket", "hangi sirket", "hangi firm", "kimden yaz", "hangi şirketten")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı hangi şirketten yazdığını soruyor.\n"
                "Sadece şirket ismini söyle, neden yazdığını bir cümleyle açıkla ve robotik 'nasıl yardımcı olabilirim' deme.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking which company you are from.\n"
            "Answer with the company name and why you reached out. Do not say a generic 'how can I help'.\n"
        )

    if any(
        token in normalized_burst
        for token in (
            "formda",
            "formdaki",
            "belirtmistim",
            "belirtmiştim",
            "zaten yaziyor",
            "zaten yazıyor",
            "sizde var",
            "zaten var",
        )
    ):
        if _is_turkish(language):
            budget_hint = ""
            if has_form_budget:
                budget_hint = (
                    " Kullanıcı formdaki bütçeyi sorarsa formdaki net bütçe sinyalini kısa ve doğrudan söyle."
                )
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı bazı bilgileri formda veya önceki mesajlarda zaten verdiğini hatırlatıyor.\n"
                "Bu turda aynı bilgiyi tekrar isteme. Önce hangi veriyi sorduysa onu kısa ve net cevapla."
                f"{budget_hint}\n"
                "Formda amaç / kullanım şekli görünüyorsa bunu kullan; yatırım mı, yaşam mı, tatil evi mi diye aynı şeyi yeniden sorma.\n"
                "Ana soruyu cevaplamadan yeni qualification sorusuna geçme.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is reminding you that some information already exists in the form or prior messages.\n"
            "Answer the specific data question first and do not ask for the same information again in this turn.\n"
        )

    if any(token in normalized for token in ("iyiyim", "iyiyiz", "iyi siz", "iyi sizler", "siz nasılsınız", "siz nasilsiniz", "nasılsınız", "nasilsiniz", "teşekkür", "tesekkur")):
        if _is_turkish(language):
            if buyer_segment == "holiday_home":
                return (
                    "\n[BU TURDA NE YAPMALISIN]\n"
                    "Kullanıcı sadece hal hatır / nezaket cevabı veriyor.\n"
                    "Bu lead tatil evi alıcısına yakın görünüyor; yatırım mı kullanım mı sorusunu ilk turda zorlama.\n"
                    "Kısa ve sıcak cevap ver. Kıbrıs tarafını biraz araştırıp araştırmadığını doğal ve sade bir soruyla aç. Sonra kullanım dönemine zemin hazırla.\n"
                    "Bu turda kapanış yapma, fiyat dökme, proje önermeye başlama ve en fazla tek küçük soru sor.\n"
                )
            if buyer_segment == "investor" and purpose_already_signaled:
                return (
                    "\n[BU TURDA NE YAPMALISIN]\n"
                    "Kullanıcı sadece hal hatır / nezaket cevabı veriyor.\n"
                    "Bu lead yatırımcıya yakın görünüyor ve amaç sinyali zaten konuşmaya girmiş durumda.\n"
                    "Bu turda amaç tekrar sorma; Kıbrıs yatırım tarafını biraz araştırıp araştırmadığını sade şekilde sor.\n"
                    "Kısa ve sıcak cevap ver, en fazla tek küçük soru sor.\n"
                )
            if buyer_segment == "residence" and purpose_already_signaled:
                return (
                    "\n[BU TURDA NE YAPMALISIN]\n"
                    "Kullanıcı sadece hal hatır / nezaket cevabı veriyor.\n"
                    "Bu lead yaşam / oturum tarafına yakın görünüyor ve amaç sinyali zaten var.\n"
                    "Bu turda 'yatırım mı, yaşam mı' veya 'kendiniz için mi bakıyorsunuz' diye geri dönme.\n"
                    "Kısa ve sıcak cevap ver. Sonra Kıbrıs tarafını biraz araştırıp araştırmadığını sade şekilde sor.\n"
                    "Bu turda Airbnb, kira getirisi veya uzaktan yönetim diline sapma.\n"
                )
            if purpose_already_signaled:
                return (
                    "\n[BU TURDA NE YAPMALISIN]\n"
                    "Kullanıcı sadece hal hatır / nezaket cevabı veriyor.\n"
                    "Amaç sinyali zaten var veya konuşmaya girmiş durumda; bu turda yatırım mı kullanım mı sorusunu tekrar sorma.\n"
                    "Kısa ve sıcak cevap ver. Öncelik Kıbrıs tarafını biraz araştırıp araştırmadığını sade ve doğal bir soruyla açmak olsun.\n"
                    "Bu turda lokasyon karşılaştırmasına sapma.\n"
                    "Bu turda kapanış yapma, fiyat dökme, sert qualification yapma ve en fazla tek küçük soru sor.\n"
                )
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı sadece hal hatır / nezaket cevabı veriyor.\n"
                "Kısa ve sıcak cevap ver. Sonra sohbeti doğalca aç.\n"
                "Bu turda kapanış yapma, 'nasıl yardımcı olabilirim' deme, fiyat dökme ve sert qualification sorusu sorma.\n"
                "Bu turda en fazla tek küçük soru sor.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is only replying politely.\n"
            "Answer warmly and gently open the conversation.\n"
            "Do not close the conversation, do not say 'how can I help', do not dump prices, and do not hard-qualify in this turn.\n"
        )

    if any(
        token in normalized_burst
        for token in (
            "karar verdigimi soylemedim",
            "karar verdiğimi söylemedim",
            "bilgi aliyorum",
            "bilgi alıyorum",
            "bilgi al",
            "sadece bilgi",
            "soruyorum ha",
            "daha karar vermedim",
            "karar vermedim",
        )
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı satın alma kararı verdiğini söylemediğini, şu an bilgi topladığını düzeltiyor.\n"
                "Kısa bir anlayış göster ve varsayımı geri çek. Satın alacakmış gibi, teklif kesinleşmiş gibi veya taşınma planı varmış gibi konuşma.\n"
                "Önce sorduğu bilgiyi ver. Bu turda baskı kurma; gerekirse en fazla tek yumuşak soru sor, istemezsen hiç sorma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is clarifying that they are only gathering information and have not decided to buy.\n"
            "Acknowledge that and de-escalate. Do not speak as if the purchase is already in motion.\n"
        )

    if any(token in normalized for token in ("bakiniyorum", "bakınıyorum", "arastiriyorum", "araştırıyorum", "degerlendiriyorum", "değerlendiriyorum", "daha bilgim yok", "daha bilgim yok", "fikrim yok", "karar vermedim", "henuz", "henüz")):
        if _is_turkish(language):
                return (
                    "\n[BU TURDA NE YAPMALISIN]\n"
                    "Kullanıcı hâlâ keşif / araştırma aşamasında olduğunu söylüyor.\n"
                    "Bu turda formdaki tam bütçe, oda sayısı veya lokasyon sinyallerini konuşmaya taşıyıp 'size uygun örnek' diye fazla kesin konuşma.\n"
                    "Önce kısa çerçeve ver: özellikle Çatalköy / Esentepe hattında denize yakın projeleriniz olduğunu doğalca söyle; Girne tarafını sadece daha merkez erişim için kısa bir referans olarak anabilirsin.\n"
                    "Bunu karşılaştırma listesine çevirme ve bu turda kullanıcıya lokasyon seçtirmeye çalışma.\n"
                    "Kullanıcı açıkça proje detayı istemediyse tam proje + tam ünite + fiyat kombinasyonuna atlama. En fazla 2 kısa cümle kur ve mümkünse bu turda soru sorma.\n"
                )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is still in an early research phase.\n"
            "Do not overfit to exact form details or jump into a specific project/unit/price combination yet.\n"
            "Give a short simple frame first.\n"
        )

    if any(
        token in normalized_burst
        for token in (
            "tatile geldik",
            "tatile gelmiştik",
            "tatile gelmistik",
            "beğendik",
            "begendik",
            "huzur",
            "denizi çok güzeldi",
            "denizi cok guzeldi",
            "çok güzeldi",
            "cok guzeldi",
        )
    ):
        if _is_turkish(language):
            if purpose_already_signaled and buyer_segment == "investor":
                next_step = (
                    "Formda amaç zaten görünüyorsa aynı şeyi tekrar sorma; tek küçük adım olarak yatırım modeline veya zamanlamaya geç.\n"
                )
            elif purpose_already_signaled and buyer_segment == "holiday_home":
                next_step = (
                    "Formda amaç zaten görünüyorsa aynı şeyi tekrar sorma; tek küçük adım olarak kullanım dönemi veya ulaşım rahatlığına geç.\n"
                )
            elif purpose_already_signaled and buyer_segment == "residence":
                next_step = (
                    "Formda amaç zaten görünüyorsa aynı şeyi tekrar sorma; tek küçük adım olarak taşınma zamanı veya günlük yaşamda en önemli ihtiyacı sor.\n"
                )
            else:
                next_step = "En fazla tek doğal soru sor; amaç veya kullanım şeklini nazikçe netleştir.\n"
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı Kıbrıs'la ilgili iyi bir ilk izlenim paylaşıyor.\n"
                "Buna insan gibi karşılık ver: Esentepe / Çatalköy sahil tarafının sakin, denize yakın ve keyifli hissettirdiğini kısa şekilde söyle. Reklam metni gibi şişirme yapma.\n"
                f"{next_step}"
                "Bu turda proje adı, fiyat listesi veya sert qualification'a atlama.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is sharing a positive first impression about Cyprus.\n"
            "Acknowledge that in a natural human way, briefly reflect the coastal feel of the area, and avoid jumping straight into a hard qualification move.\n"
        )

    if (
        any(token in normalized_burst for token in ("airbnb", "studio daire", "stüdyo daire", "3 tane", "uc tane", "üç tane"))
        and any(token in normalized_burst for token in ("yönetim", "yonetim", "hizmet", "polonya", "yurtdışında", "yurtdisinda"))
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı çok parçalı bir yatırım senaryosu soruyor: birkaç stüdyo, Airbnb kullanımı ve uzaktan yönetim.\n"
                "Önce TÜM sorularını sırayla cevapla: birkaç küçük birim mantığını genel çerçevede değerlendir, uzaktan yönetim / hizmet varsa doğrulanmış kapsam kadar söyle, belirsiz oran veya garanti dili kullanma.\n"
                "Ana soruyu cevaplamadan teklif, ödeme planı veya satın almış gibi konuşmaya geçme. En fazla tek kısa takip sorusu sor.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking a multi-part investor question about multiple studios, Airbnb use, and remote management.\n"
            "Answer all parts first before moving to quote or payment-plan language.\n"
        )

    if any(token in normalized_burst for token in ("siz hangisini önerirsiniz", "siz hangisini onerirsiniz", "hangi daha mantıklı", "hangi daha mantikli", "sizce hangisi", "siz hangisini tavsiye")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı senden net öneri istiyor.\n"
                "Topu tekrar kullanıcıya atma. Konuşmada geçen seçenekler içinden birini kısa ve net öner; nedenini 1-2 cümlede açıkla.\n"
                "Aynı turda kullanıcı ikinci bir konu da açtıysa (ör. Airbnb yönetimi, kullanım şekli, ödeme mantığı), onu da cevapla.\n"
                "Önce öneriyi ver, sonra en fazla tek doğal takip sorusu sor.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user wants a direct recommendation.\n"
            "Do not bounce it back. Recommend one option from the conversation first, explain why briefly, then answer any second question they asked.\n"
        )

    if any(
        token in normalized_burst
        for token in ("konumu aynı mı", "konumu ayni mi", "hangisinin konumu daha iyi", "aynı konumda mı", "ayni konumda mi")
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı konum karşılaştırması soruyor.\n"
                "Önce bunu kısa ve net cevapla. Aynı proje içindeyse bunu dürüstçe söyle; varsa farkı sadece doğrulanmış ölçüde anlat.\n"
                "Bu turda cevabın sonuna yeni seçim sorusu ekleme. 'Hangisi size daha uygun?' diye hemen geri zıplama.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking for a location comparison.\n"
            "Answer that directly and do not bounce back into a forced choice question in the same turn.\n"
        )

    if any(token in normalized_burst for token in ("iki konu hakkında", "iki konu hakkinda", "ikisinin de", "ikisi hakkında", "ikisi hakkinda", "ikiside", "ikisi de")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı aynı anda iki başlık hakkında bilgi istiyor.\n"
                "İkisini de sırayla cevapla. Tek bir parçayı cevaplayıp susma.\n"
                "Gerekirse yanıtı 2 kısa balona böl: önce öneri/ana cevap, sonra ikinci konu.\n"
                "Bu turda yeni qualification sorusu zorlama.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user wants information on two topics in the same turn.\n"
            "Answer both in order. Do not answer one and stop.\n"
        )

    # Use normalized_burst to catch multi-message sequences like "yok" + "maalesef olmadı" + "kumara geldik"
    _burst = normalized_burst
    if any(token in _burst for token in ("kıbrıs hakkında hiçbir fikrim yok", "kibris hakkinda hicbir fikrim yok", "nasıl lokasyon", "nasil lokasyon", "hiç bilmiyorum", "hic bilmiyorum", "pek bilmiyorum", "bilmiyorum", "çok bilgim yok", "cok bilgim yok", "fikrim yok", "çok gezemedim", "cok gezemedim", "bir iki kez", "araştırmadım", "arastirmadim", "maalesef olmadı", "maalesef olmadi", "olmadı maalesef", "kumara geldik", "kumara geliyorum", "tatile geldik", "tatile geliyorum", "çok araştırmadım", "cok arastirmadim", "detaylı araştırmadım", "detayli arastirmadim")) or (
        any(w in _burst for w in ("yok", "hayır", "hayir", "pek yok", "maalesef", "olmadı", "olmadi"))
        and any(token in previous_assistant for token in ("kıbrıs", "kibris", "göz at", "goz at", "araştırma", "arastirma", "fırsatınız", "firsatiniz"))
    ):
        if _is_turkish(language):
            if buyer_segment == "residence":
                return (
                    "\n[BU TURDA NE YAPMALISIN — BU TALIMAT DIGER TUM KURALLARI GECERSIZ KILAR]\n"
                    "Kullanıcı Kıbrıs'ı çok tanımıyor ve bu lead yaşam / oturum tarafına yakın.\n"
                    "1) Kısa ve doğal bir çerçeve ver: Çatalköy-Esentepe sahil tarafının sakin, denize yakın ve Girne merkeze çok kopuk olmayan bir taraf olduğunu söyle.\n"
                    "2) Formda amaç zaten görünüyorsa aynı şeyi tekrar sorma. Tek soru olarak taşınma zamanına ya da günlük yaşamda en önemli önceliğe geç.\n"
                    "YAPMA: yatırım dili, Airbnb/ROI, fiyat listesi, proje adı, 'kendiniz mi yaşayacaksınız' sorusu.\n"
                    "Toplam en fazla 3 kısa cümle.\n"
                )
            if buyer_segment == "holiday_home":
                return (
                    "\n[BU TURDA NE YAPMALISIN — BU TALİMAT DİĞER TÜM KURALLARI GEÇERSİZ KILAR]\n"
                    "Kullanıcı Kıbrıs'ı çok tanımıyor ve tatil evi tarafına yakın.\n"
                    "1) Çatalköy-Esentepe hattında denize yakın projeleriniz olduğunu söyle. Girne merkeze 20 dk mesafede.\n"
                    "2) TEK doğal soruyla ilerlet — 'Nasıl bir kullanım düşünüyorsunuz?' gibi.\n"
                    "YAPMA: satış pitch'i, yatırım modeli sorusu, fiyat, proje adı.\n"
                    "Toplam en fazla 3 kısa cümle.\n"
                )
            if buyer_segment == "investor":
                return (
                    "\n[BU TURDA NE YAPMALISIN — BU TALİMAT DİĞER TÜM KURALLARI GEÇERSİZ KILAR]\n"
                    "Kullanıcı Kıbrıs'ı çok bilmiyor ve bu lead yatırımcıya yakın.\n"
                    "1) Çatalköy-Esentepe hattında denize yakın projeleriniz olduğunu kısa ve sade şekilde söyle. Girne merkeze 20 dk mesafede olduğunu anabilirsin.\n"
                    "2) Formda yatırım amacı zaten görünüyorsa bunu tekrar sorma. Tek doğal soruyla yatırım modeline veya zamanlamaya geç.\n"
                    "Kullanıcı Kıbrıs'ı bilmediğini söylediyse 'hangi bölgeyi tercih edersiniz' diye geri sorma; bu turda lokasyon seçtirme yapma.\n"
                    "YAPMA: ROI rakamı, kira garantisi, fiyat listesi, tam proje pitch'i.\n"
                    "Toplam en fazla 3 kısa cümle.\n"
                )
            return (
                "\n[BU TURDA NE YAPMALISIN — BU TALİMAT DİĞER TÜM KURALLARI GEÇERSİZ KILAR]\n"
                "Kullanıcı Kıbrıs'ı bilmiyor.\n"
                "1) Kısa ve samimi şekilde yönlendir — Çatalköy-Esentepe hattında denize yakın projeleriniz olduğunu söyle. Girne merkeze 20 dk mesafede.\n"
                "2) Hemen ardından TEK doğal soruyla konuşmayı ilerlet — 'Nasıl bir şey düşünüyorsunuz?' veya 'Bunu daha çok yatırım için mi düşünüyorsunuz?' gibi.\n"
                "Kullanıcı açıkça Kıbrıs'ı bilmediğini söylediyse 'hangi bölgeyi tercih edersiniz' veya benzeri lokasyon seçimi SORMA.\n"
                "YAPMA: satış pitch'i ('değer artışı', 'kiralama potansiyeli', 'yatırımcıların gözdesi'), yatırım modeli sorusu (erken al-sat / kira / Airbnb listesi), fiyat, proje adı.\n"
                "Toplam en fazla 3 kısa cümle.\n"
                "Örnek: 'Hiç sorun değil 🙂 Girne'ye 20 dk mesafede, Çatalköy-Esentepe hattında denize yakın projelerimiz var. Sakin ve keyifli bir taraf. Nasıl bir şey düşünüyorsunuz?'\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user does not know Cyprus and wants guidance.\n"
            "Do not force them to choose a location. Briefly explain that most relevant options are on the Catalkoy / Esentepe coastal side, and mention Kyrenia only as a short access reference if needed.\n"
            "Do not dump prices, payment plans, or a full project pitch in this turn.\n"
        )

    if any(token in normalized for token in ("evet", "anladım", "anladim", "güzelmiş", "guzelmis", "tamam", "iyiymiş", "iyiymis")) and any(
        token in previous_assistant
        for token in ("esentepe", "girne", "çatalköy", "catalkoy", "kıbrıs", "kibris")
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı verdiğin kısa Kıbrıs / lokasyon çerçevesini anladığını söylüyor.\n"
                "Aynı lokasyon açıklamasını tekrar etme ve 'Girne mi Esentepe mi' gibi bir soruya dönme.\n"
                "Bu turda bir sonraki eksik sinyale geç: en uygunu zamanlama, kullanım şekli veya bütçe esnekliği. Tek kısa soru sor.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user has acknowledged your short orientation about Cyprus or location.\n"
            "Do not repeat the same explanation or turn it into a location-choice question. Move to the next missing signal with one short question.\n"
        )

    if (
        any(token in normalized_burst for token in ("dusuneyim", "düşüneyim", "sonra bakariz", "sonra bakarız", "inceleyeyim", "bakayim", "bakayım"))
        and any(
            token in previous_assistant
            for token in (
                "teklif",
                "örnek",
                "ornek",
                "ödeme",
                "odeme",
                "paylaş",
                "paylas",
                "gönder",
                "gonder",
            )
        )
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı yumuşak şekilde beklemek veya düşünmek istediğini söylüyor.\n"
                "Bunu satın alma ilerliyor gibi okuma. Baskıyı düşür, kısa bir anlayış göster ve kapıyı açık bırak.\n"
                "Bu turda yeni qualification sorusu sorma, handoff yapma veya aynı onayı tekrar isteme.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user wants to pause and think about it.\n"
            "Treat this as a soft hold, not momentum toward purchase. Lower pressure and do not ask a new qualification question.\n"
        )

    if (
        any(token in previous_assistant for token in ("görüş", "gorus", "telefon", "video", "randevu", "danışman", "danisman", "temsilci", "uzman"))
        and any(token in normalized_burst for token in ("olur", "tamam", "yarın", "yarin", "öğleden sonra", "ogleden sonra", "sabah", "uygun"))
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı insanla devam etme fikrine onay verdi.\n"
                "Bu noktadan sonra chat içinde sekreter gibi tarih-saat pazarlığı yapma. Gereksiz back-and-forth yaratma.\n"
                "Kısa ve sıcak şekilde ekipteki ilgili satış danışmanının mevcut numara / bu kanal üzerinden devam edeceğini söyle.\n"
                "Numara lead/form içinde görünüyorsa telefonu yeniden isteme.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user agreed to continue with a human follow-up.\n"
            "Do not keep negotiating schedule details in chat. Briefly say the relevant sales consultant will continue on the current channel/number.\n"
        )

    if (
        any(token in normalized_burst for token in ("olur", "olut", "isterim", "tamam", "gonder", "gönder", "paylas", "paylaş"))
        and any(
            token in previous_assistant
            for token in (
                "teklif",
                "örnek",
                "ornek",
                "ödeme planı",
                "odeme plani",
                "ödeme",
                "odeme",
                "hazırlayayım",
                "hazirlayayim",
                "hazırlayıp",
                "hazirlayip",
                "paylaşayım",
                "paylasayim",
                "paylaş",
                "paylas",
                "ileteyim",
                "ilet",
                "göndereyim",
                "gondereyim",
            )
        )
    ):
        if _is_turkish(language):
            existing_contact_line = (
                "İletişim kanalı lead/form içinde görünüyorsa e-posta veya telefonu yeniden isteme; mevcut kanaldan paylaşabileceğini söyle.\n"
                if has_contact_details
                else "İletişim kanalı görünmüyorsa sadece hangi kanaldan paylaşmamı istersiniz diye sor; aynı onayı tekrar isteme.\n"
            )
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı teklif veya paylaşım için onay verdi.\n"
                "Bunu satın alma kararı gibi okuma; bu sadece devam etmek için izin. Aynı 'olur mu / ister misiniz' onayını tekrar isteme.\n"
                f"{existing_contact_line}"
                "Doğrulanmamış fiyat kombinasyonu, taksit vadesi veya özel teklif detayı UYDURMA.\n"
                "Bu onay yazım hatalı bile gelse ('olut' gibi) izin olarak yorumla; tekrar peşinat oranı veya başka onay isteme.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user has already consented to receive the quote or information.\n"
            "Treat that as permission to proceed, not as a purchase commitment, and do not ask for the same consent again.\n"
        )

    if (
        any(token in normalized_burst for token in ("bu bilgiler var sizde", "zaten sizde", "sizde var", "zaten var"))
        and any(token in previous_assistant for token in ("e-posta", "eposta", "email", "mail", "telefon", "numara"))
    ):
        if _is_turkish(language):
            if has_contact_details:
                return (
                    "\n[BU TURDA NE YAPMALISIN]\n"
                    "Kullanıcı iletişim bilgilerinin zaten sizde olduğunu söylüyor.\n"
                    "Bunu kabul et ve aynı bilgiyi tekrar isteme. Varsa formdaki mevcut kanaldan paylaşabileceğini söyle.\n"
                    "Bu turda savunmaya geçme veya yeni qualification sorusu sorma.\n"
                )
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı iletişim bilgilerinin zaten sizde olduğunu söylüyor.\n"
                "Kısa ve sakin kal; sistemde net görünmüyorsa bunu dürüstçe belirt ama aynı bilgiyi ısrarla isteme. Sadece paylaşım tercihini sorabilirsin.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user says you already have their contact information.\n"
            "Acknowledge that and do not re-request the same contact detail.\n"
        )

    if (
        any(token in normalized for token in ("anladım", "anladim", "tamam", "oldu", "güzel", "guzel", "iyi", "iyiymiş", "iyiymis"))
        and previous_assistant
        and len(normalized.split()) <= 4
        and not any(
            token in normalized
            for token in ("?", "hangi", "öner", "oner", "bilgi", "proje", "detay", "var mı", "var mi", "neler var")
        )
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN — BU TALİMAT DİĞER TÜM KURALLARI GEÇERSİZ KILAR]\n"
                "Kullanıcı onay verdi. Sohbet bitmiyor, yön sende.\n"
                "YAPMA: kapanış dili ('memnun oldum'), aynı konuyu tekrarlama, daha önce sorduğun soruyu tekrar sorma.\n"
                "YAP: Kısa geçiş yap ve BİR SONRAKİ eksik sinyale geç. Şimdiye kadar konuşulmamış konulardan birini seç:\n"
                "- Henüz bütçe konuşulmadıysa: 'Bütçe olarak nasıl düşünüyorsunuz?'\n"
                "- Henüz zamanlama konuşulmadıysa: 'Zamanlamanız nasıl, yakın vadede mi düşünüyorsunuz?'\n"
                "- Henüz tip netleşmediyse: 'Tek büyük birim mi yoksa birkaç küçük birim mi düşünüyorsunuz?'\n"
                "- Müşteri proje görmek isterse: KB'den uygun projeyi doğal cümleyle anlat.\n"
                "Tek kısa soru sor.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is simply acknowledging the information you gave.\n"
            "Do not use closing language like 'glad to hear' or 'thank you' as if the conversation is ending.\n"
            "Acknowledge briefly and move the conversation forward with one short next-step question.\n"
        )

    if (
        has_form_budget
        and any(
            token in normalized_burst
            for token in (
                "bütçem ne kadardı",
                "butcem ne kadardi",
                "bütçem kaçtı",
                "butcem kacti",
                "bütçem neydi",
                "butcem neydi",
                "bütçemi hatırlat",
                "butcemi hatirlat",
            )
        )
    ):
        if _is_turkish(language):
            extra_payment = ""
            if any(token in normalized_burst for token in ("peşinat", "pesinat", "ödeme", "odeme", "taksit", "kredi", "finansman")):
                extra_payment = (
                    " Kullanıcı aynı turda peşinat/ödeme de soruyorsa ikisini de cevapla: bütçeyi formdan kısa söyle, peşinat için ise sadece genel çerçeve ver.\n"
                    "Net yüzde, 'genelde %20-30' gibi yuvarlak oran veya vade UYDURMA; 'projeye göre değişiyor, net oranı proje bazında paylaşabilirim' çizgisinde kal.\n"
                )
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı formdaki bütçe bilgisini hatırlatmanı istiyor.\n"
                "Önce formdaki net bütçe sinyalini kısa ve doğrudan söyle. Aynı bilgiyi yeniden sorma.\n"
                f"{extra_payment}"
                "Bu turda yeni qualification sorusu sorma; önce sorulan bilgiyi tamamla.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking you to remind them of the budget already visible in the form.\n"
            "State that budget briefly first and do not ask for it again.\n"
        )

    if any(token in normalized for token in ("sizin mi", "sizin proje", "size ait", "ait mi", "sizin mi?", "your project")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı belirli bir site veya projenin size ait olup olmadığını soruyor.\n"
                "Önce sadece bu soruyu cevapla. Doğrulanmış KB/RAG'de varsa kısa ve net söyle; yoksa 'şu an aktif portföyde bu isim görünmüyor' gibi doğal cevap ver.\n"
                "Bu turda 'veritabanımda', 'sistemimde' gibi ifadeler kullanma ve aynı mesajda qualification sorusu sorma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking whether a specific site or project belongs to your company.\n"
            "Answer that question first using only verified KB/RAG. If it is not there, say it is not visible in the active portfolio.\n"
            "Do not ask a qualification question in the same turn.\n"
        )

    if any(token in normalized for token in ("sizin ne var", "neler var", "bilgi alabilir", "siz ne öner", "siz ne oner", "detay verir", "ne önerirsiniz", "ne onerirsiniz", "var mı projeniz", "var mi projeniz", "projeniz var mı", "projeniz var mi", "uygun proje var mı", "uygun proje var mi", "studio daire", "stüdyo daire", "studio olur", "stüdyo olur")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı somut öneri veya proje bilgisi istiyor.\n"
                "Eğer müşteri henüz tipini netleştirmediyse hemen tek proje ismi fırlatma. Önce 2-3 anlaşılır kategoriyle yön ver: ör. stüdyo / 1+1, orta ölçek daire, villa gibi.\n"
                "Müşteri belirli bir tipe yaklaştıysa o zaman varsa en uygun 1 projeyi söyle, 1-2 özelliğini ver, kısa kal. RAG dışında hiçbir proje uydurma.\n"
                "Müşteri fiyat sormadıysa fiyatı söyleme.\n"
                "Bu turda müşteriyi hemen '2+1 mi 3+1 mi' diye daraltma. Önce yön ver, kısa bilgi ver, sonra dur.\n"
                "Ana soruyu cevaplamadan yeni qualification sorusuna geçme.\n"
                "Tonun danışman gibi olsun: yönlendir, kısa karşılaştır, sonra en fazla tek doğal soru sor.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user wants concrete project suggestions.\n"
            "If their property type is still broad, guide them with 2-3 simple categories first instead of jumping to one exact project. If it is already clear, mention the single best matching project with 1-2 key features.\n"
        )

    if any(
        token in normalized_burst
        for token in ("bütçeme göre", "butceme gore", "bana sunabileceğiniz", "bana sunabileceginiz", "sunabileceğiniz", "sunabileceginiz", "neler var")
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı bütçesine göre hangi seçenekleri sunabileceğini soruyor.\n"
                "Bunu dar bir seçim sorusuna sıkıştırma. Hemen '2+1 mi 3+1 mi' diye zorlama.\n"
                "Eğer doğrulanmış bütçe uyumu net değilse 'bütçenize uygun' deme. Bunun yerine bütçeye yakın görünen 1-2 yön veya kategori öner ve kısa kal.\n"
                "Bilgi verdikten sonra aynı turda seçim yaptırmaya çalışma; müşteri devam sorusu soracaktır.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking what you can offer around their budget.\n"
            "Do not force a narrow unit choice right away. Offer one or two directions first and avoid claiming budget fit unless it is clearly verified.\n"
        )

    if any(token in normalized for token in ("fiyatı ne kadar", "fiyati ne kadar", "fiyat ne kadar", "kaç euro", "kac euro", "kaç para", "kac para", "evlerin fiyatı", "evlerin fiyati")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı fiyat soruyor.\n"
                "Önce doğrulanmış fiyatı veya fiyat aralığını net ve kısa söyle. 'Bütçenize uygun' gibi yuvarlak cevap verme.\n"
                "Kullanıcı fiyat sormuşken aynı mesajda qualification sorusuna atlama.\n"
                "Eğer elde sadece proje genel fiyat aralığı varsa bunu dürüstçe proje aralığı olarak söyle; tek ünite fiyatı gibi sunma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking for the price.\n"
            "Answer with the verified price or price range first. Do not dodge with a vague budget-fit reply.\n"
            "Do not ask a qualification question in the same turn.\n"
        )

    if any(token in normalized for token in ("seçenekler ne", "secenekler ne", "hangi üniteler", "hangi uniteler", "properties verebilir", "property verebilir", "ünite tipleri", "unit type", "projeler hakkında", "projeler hakkinda", "biraz daha bilgi", "daha fazla bilgi")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı daha fazla proje detayı veya proje içi seçenekleri istiyor.\n"
                "Önce bilgi ver. Varsa doğrulanmış RAG'den sadece ilgili proje içindeki ünite tiplerini ve fiyat aralığını doğal cümlelerle paylaş.\n"
                "Ham markdown, madde listesi veya katalog dili gibi görünme; bilgiyi WhatsApp'ta konuşuyormuş gibi 1-2 kısa baloncukta özetle.\n"
                "Bu turda yeni qualification sorusu sorma. Kullanıcı önce bilgiyi almak istiyor.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user wants more detail about project options.\n"
            "Answer with the verified unit types and price ranges in a natural conversational way, not as a raw markdown dump.\n"
            "Do not ask a qualification question in this turn.\n"
        )

    if any(token in normalized for token in ("imkanları ne", "imkanlari ne", "tesisleri", "tesisler", "olanakları", "olanaklari", "facility", "amenities", "özellikleri ne", "ozellikleri ne")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı tesis, imkan veya proje özelliklerini soruyor.\n"
                "Önce bunu cevapla. Sadece doğrulanmış RAG/KB'de açıkça görünen tesisleri söyle; projede yazmayan havuz, güvenlik, fitness, çocuk parkı, otopark gibi detayları UYDURMA.\n"
                "Bilgi eksikse dürüstçe 'şu an önümde net tesis listesi görünmüyor, kontrol edip paylaşayım' de.\n"
                "Bu turda qualification sorusu veya zamanlama sorusu ekleme.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking about facilities or amenities.\n"
            "Answer that first using only explicit verified KB/RAG details. If the exact facility list is not visible, say you can confirm it.\n"
            "Do not ask a qualification question in this turn.\n"
        )

    if any(token in normalized for token in ("denize ne kadar", "havaalanına", "havaalanina", "havalimanına", "havalimanina", "şehre ne kadar", "sehre ne kadar", "yakın mı", "yakin mi", "uzak mı", "uzak mi", "mesafe", "yakınlık", "yakinlik")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı yakınlık, uzaklık veya mesafe soruyor.\n"
                "Önce bunu cevapla. Sadece RAG'de temiz ve doğrulanmış görünen mesafe verilerini kullan; şüpheli veya karışık veri varsa emin değilmiş gibi dürüst kal.\n"
                "Bu turda qualification sorusu sorma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking about proximity or distance.\n"
            "Answer first using only clean verified distance data from RAG. If the data looks unclear, be honest.\n"
            "Do not ask a qualification question in this turn.\n"
        )

    if any(token in normalized for token in ("teras", "bahçe", "bahce", "manzara", "içi nasıl", "ici nasil", "içerisi", "icerisi", "yüksek tavan", "yuksek tavan", "dublex", "duplex", "çatı", "cati")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı ünitenin yaşam detaylarını soruyor.\n"
                "Önce bunu cevapla. Sadece property description içinde açıkça geçen teras, bahçe, manzara, açık plan, yüksek tavan gibi detayları kullan.\n"
                "Description'da geçmeyen metrekare, oda dağılımı veya iç mimari detayı UYDURMA. Bu turda qualification sorusu sorma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking about unit-level lifestyle details.\n"
            "Answer using only explicit details from the property descriptions such as terrace, garden, view, open-plan layout, or high ceilings.\n"
            "Do not invent square meters or interior details, and do not ask a qualification question in this turn.\n"
        )

    if any(token in normalized for token in ("büyüklük", "buyukluk", "kaç m2", "kac m2", "metrekare", "m2", "3+1", "4+1")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı büyüklük veya tip seçeneklerini soruyor.\n"
                "Önce bunu kısa ve net cevapla: doğrulanmış tipleri ve yaklaşık aralığı söyle. Sonra gerekiyorsa sadece tek somut tercih sorusu sor: '3+1 tarafı mı daha yakın size, yoksa 4+1 mi?' gibi.\n"
                "Bu turda yıl içinde ne zaman kullanacağını, Airbnb düşünüp düşünmediğini veya yatırım modelini sorma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking about size or unit-type options.\n"
            "Answer that first, then if needed ask only one concrete preference question. Do not pivot into usage-season or investment questions in this turn.\n"
        )

    if any(token in normalized for token in ("link var mı", "link var mi", "broşür", "brosur", "pdf", "sunum", "proje linki", "website", "web sitesi")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı link, broşür veya materyal istiyor.\n"
                "Önce bu talebi cevapla. Doğrulanmış link veya doküman prompt içinde açıkça yoksa 'hemen gönderiyorum' deme.\n"
                "Bunun yerine kısa ve dürüst ol: 'şu an link önümde görünmüyor, kontrol edip paylaşayım' gibi.\n"
                "Bu turda call iteleme, qualification sorusu veya handoff yapma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking for a link, brochure, or material.\n"
            "Answer that request first. If no verified link or document is visible, do not promise to send it immediately.\n"
                "Do not push a call, ask a qualification question, or hand off in this turn.\n"
        )

    if any(token in normalized for token in ("resim", "görsel", "gorsel", "foto", "fotoğraf", "fotograf", "render", "images", "image", "photo")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı görsel, resim veya fotoğraf istiyor.\n"
                "Önce bu talebi cevapla. Prompt içinde doğrulanmış görsel/link yoksa 'şimdi gönderiyorum' deme ve onun yerine proje bilgisini tekrar etmeye kaçma.\n"
                "Kısa ve dürüst kal: görselleri ayrıca paylaşman gerektiğini veya kontrol edip ileteceğini söyle. Bu turda qualification sorusu sorma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking for visuals or photos.\n"
            "Answer that request first. If no verified visual asset or link is available in context, do not pretend to send it immediately.\n"
            "Do not ask a qualification question in this turn.\n"
        )

    if any(token in normalized for token in ("bilgi vermiyorsunuz", "bilgi vermedin", "hiç bilgi vermiyorsunuz", "hic bilgi vermiyorsunuz", "cevap vermiyorsunuz", "yanıt vermiyorsunuz", "yeterince bilgi yok")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı bilgi alamadığını söyleyerek hafif hayal kırıklığı gösteriyor.\n"
                "Önce bunu kabul et ve savunmaya geçme. Muhtemelen sorusunun bir kısmı yanıtsız kaldı; eksik kalan kısmı gerçekten tamamla.\n"
                "Gerekirse cevabı 2 kısa balona böl: ilk balon kısa kabul / düzeltme, ikinci balon somut bilgi.\n"
                "Bu turda soru sorma, handoff yapma veya satış materyali sözü verme. Önce güveni toparla ve bilgi ver.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is frustrated that they are not getting enough information.\n"
            "Acknowledge that briefly, then provide the concrete information they asked for.\n"
            "Do not ask a question or hand off in this turn.\n"
        )

    if any(token in normalized_burst for token in ("ne uzadı", "ne uzadi", "baydın", "baydin", "ne uzattın", "ne uzattin", "baydınız", "baydiniz")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı sürecin gereksiz uzadığını söylüyor.\n"
                "Kısa bir özür dile ve friksiyonu hemen kes. Yeni soru sorma, saat-numara-teyit isteme.\n"
                "İnsanla devam noktası geldiyse kısa şekilde ilgili satış danışmanının mevcut kanal üzerinden devam edeceğini söyle.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is saying the process is dragging.\n"
            "Apologize briefly and remove friction immediately. Do not ask more scheduling or confirmation questions.\n"
        )

    if any(
        token in normalized
        for token in (
            "ara ara kullanım",
            "ara ara kullanim",
            "ara sıra kullanım",
            "ara sira kullanim",
            "kendim de kullan",
            "kullanım nasıl",
            "kullanim nasil",
            "kiraya vermey",
            "kira vermey",
            "kiraya vermeden",
            "kira vermeden",
            "kiraya vermek istem",
            "kira vermek istem",
            "kira düşünmüyorum",
            "kira dusunmuyorum",
        )
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı ara ara kullanım veya kiralama esnekliğini soruyor.\n"
                "Önce soruyu cevapla. Kiralama zorunluymuş gibi konuşma; kullanım ve kiralama modelinin tercihe göre şekillenebileceğini genel çerçevede anlat.\n"
                "RAG'de açıkça yazmıyorsa proje içi yönetim firması, zorunlu kiralama, garanti kira veya otomatik işletme modeli UYDURMA.\n"
                "En fazla 2 kısa cümleyle net cevap ver. Ana soruyu cevapladıktan sonra sohbeti sen yönlendir: tek kısa takip sorusu sor. En iyi sonraki adım yatırım modeli veya zamanlamadır.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking about occasional personal use or rental flexibility.\n"
            "Answer first without implying that renting is mandatory, and do not invent on-site management or guaranteed rental programs unless verified.\n"
            "After answering, ask one short follow-up to keep the conversation moving.\n"
        )

    if any(token in normalized for token in ("peşinat", "pesinat", "ödeme planı", "odeme plani", "ödeme", "odeme", "taksit", "kredi", "finansman")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı ödeme, peşinat veya finansman soruyor.\n"
                "Önce soruyu kısa ve net cevapla. Doğrulanmış RAG/KB veya pricing hint içinde açıkça yoksa net yüzde, vade veya plan UYDURMA.\n"
                "Doğrulanmamış durumda 'projeye göre değişiyor, net örnek üzerinden paylaşabilirim' çizgisinde kal.\n"
                "'Genelde %20-30', '%30 civarı' gibi yuvarlak peşinat oranları bile doğrulanmadan verme.\n"
                "Aynı mesajda call iteleme, ikinci qualification sorusu veya telefon numarası isteme.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking about payment, deposit, or financing.\n"
            "Answer briefly first. Do not invent exact percentages or terms unless they are explicitly verified in KB/RAG or pricing hints.\n"
            "Do not push for a call or ask for their phone number in the same message.\n"
        )

    if any(token in normalized for token in ("nasıl yatırım", "nasil yatirim", "kira getirisi", "getiri", "yatırım yapabilirim", "yatirim yapabilirim")):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı yatırım mantığını veya kira getirisini soruyor.\n"
                "Önce soruyu kısa cevapla. RAG'de yazmayan net getiri oranı, yüzde veya yıllık kazanç UYDURMA.\n"
                "En fazla 2 kısa cümleyle cevap ver. Sonra sadece 1 qualification adımı ilerle.\n"
                "Bu turda broşür, PDF, uzman ekip, yatırım analizi gibi satış materyali dili kullanma.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is asking how the investment works or about rental return.\n"
            "Answer briefly first. Do not invent exact return percentages or annual profit if they are not in RAG.\n"
            "Keep it to at most 2 short sentences, then move only one qualification step forward.\n"
        )

    if (
        any(token in normalized_burst for token in ("taşınmayacağım", "tasinmayacagim", "taşınmay", "tasinmay", "oturmayacağım", "oturmayacagim", "oturmay"))
        or (
            "airbnb" in normalized_burst
            and any(token in normalized_burst for token in ("yapacağız", "yapacagiz", "yapac", "işlet", "islet"))
        )
    ):
        if _is_turkish(language):
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı oturmak için değil, yatırım / işletme amaçlı düşündüğünü netleştiriyor.\n"
                "Bu turda taşınma, kullanım için teslim zamanı veya yaşam senaryosu sorma. Öncelik işletme modeli, yönetim desteği ve yatırım zamanlamasıdır.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user clarified that this is not for moving in, but for investment or operation.\n"
            "Do not ask move-in questions in this turn.\n"
        )

    if any(token in normalized for token in ("birkaç ay içinde", "birkac ay icinde", "bu yıl", "bu yil", "yazın", "yazin", "yakında", "yakinda", "hemen değil", "hemen degil")):
        if _is_turkish(language):
            if buyer_segment == "holiday_home":
                return (
                    "\n[BU TURDA NE YAPMALISIN]\n"
                    "Kullanıcı tatil evi tarafında zamanlama sinyali verdi.\n"
                    "Aynı turda zamanlamayı yeniden sorma. Bunu not al ve bir sonraki doğal adım olarak kullanım dönemi veya karar sürecini netleştir.\n"
                )
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı zamanlama sinyali verdi.\n"
                "Aynı turda zamanlamayı yeniden sorma. Bunu anlayıp not al ve sıradaki eksik qualification alanına geç.\n"
                "Eğer amaç ve zamanlama artık netse bir sonraki doğal adım bütçe teyidi veya karar verici olabilir.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user gave timing information.\n"
            "Do not ask the same timing question again. Acknowledge it and move to the next missing qualification signal.\n"
        )

    if any(token in normalized for token in ("yatırım için", "yatirim icin", "kendim de kullan", "tatile geliriz", "yazın kullan", "yazin kullan")):
        if _is_turkish(language):
            if buyer_segment == "holiday_home":
                return (
                    "\n[BU TURDA NE YAPMALISIN]\n"
                    "Kullanıcı tatil evi kullanım senaryosunu anlatıyor.\n"
                    "Bunu tekrar sorma. Kısa bir anlayış göster ve hemen proje satmaya atlama.\n"
                    "Öncelik kullanım dönemi, Kıbrıs'a hakimiyet veya satın alma zamanı gibi daha önemli sinyaller olsun; en fazla tek küçük soru sor.\n"
                )
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı amaç ve kullanım senaryosunu anlatıyor.\n"
                "Bu bilgiyi tekrar sorma. Kısa bir anlayış göster ve hemen proje satmaya atlama.\n"
                "Önce zamanlama, bütçe esnekliği veya karar ciddiyeti tarafını biraz daha netleştir; en fazla tek küçük soru sor.\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user is explaining their purpose and usage scenario.\n"
            "Do not repeat that question. Acknowledge it and avoid jumping into a project pitch immediately.\n"
            "Clarify timing, budget flexibility, or seriousness with only one small next question.\n"
        )

    if (
        any(token in normalized for token in ("deniz kenarı", "deniz kenari", "şehir merkezi", "sehir merkezi", "merkez", "sakin", "hareketli"))
        and any(token in previous_assistant for token in ("deniz kenarı", "deniz kenari", "şehir merkez", "sehir merkez", "konum", "lokasyon"))
    ):
        if _is_turkish(language):
            if buyer_segment == "holiday_home":
                return (
                    "\n[BU TURDA NE YAPMALISIN]\n"
                    "Kullanıcı tatil evi için bölge / yaşam tarzı tercihini söyledi.\n"
                    "Kısa bir yorum yap ama HEMEN proje önermeye geçme.\n"
                    "Bu turda konum tercihini anladığını göster ve doğal şekilde kullanım dönemi veya satın alma zamanına geç.\n"
                    "İstersen genel kal: 'Deniz kenarı tarafı tatil evi için gerçekten çok keyifli oluyor 🙂 Siz daha çok yılın hangi dönemlerinde kullanmayı düşünüyorsunuz?'\n"
                )
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Kullanıcı bölge / yaşam tarzı tercihini söyledi.\n"
                "Kısa bir yorum yap ama HEMEN proje önermeye geçme.\n"
                "Bu turda lokasyon tercihini anladığını göster ve doğal şekilde zamanlama sorusuna geç.\n"
                "İstersen genel kal: 'Deniz kenarı tarafında gerçekten çok güzel seçenekler oluyor 🙂 Siz bunu daha yakın vadede mi düşünüyorsunuz, yoksa biraz daha rahat bir planda mı bakıyorsunuz?'\n"
            )
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "The user gave their lifestyle or location preference.\n"
            "Acknowledge it, but do not jump into a project recommendation yet.\n"
            "Use this turn to move naturally into timing.\n"
        )

    recommended_next_question = str((champ_json or {}).get("recommended_next_question") or "").strip()
    if _is_turkish(language):
        if buyer_segment == "investor":
            segment_focus = (
                "Bu lead yatırımcı tarafına yakın görünüyor. Varsayılan olarak yatırım modelini "
                "(erken alıp teslime yakın satmak, uzun dönem kira, Airbnb / kısa dönem veya hibrit), "
                "finansman hazırlığını ve zamanlamayı doğal akışta netleştir. "
                "Formda veya açılışta yatırım sinyali varsa aynı şeyi baştan sorma."
            )
        elif buyer_segment == "holiday_home":
            segment_focus = (
                "Bu lead tatil evi tarafına yakın görünüyor. Varsayılan olarak Kıbrıs bilgisi, "
                "kullanım dönemi, uzaktan kullanım rahatlığı ve satın alma zamanına ilerle."
            )
        elif buyer_segment == "residence":
            segment_focus = (
                "Bu lead yaşam / oturum tarafına yakın görünüyor. Varsayılan olarak taşınma tarihi, "
                "günlük yaşam ihtiyacı, mahalle uyumu ve karar sürecine ilerle."
            )
        else:
            segment_focus = (
                "Segment tam net değil. Varsayılan olarak amaç, zamanlama ve bütçe hazırlığını doğal akışta netleştir."
            )

        if recommended_next_question:
            return (
                "\n[BU TURDA NE YAPMALISIN]\n"
                "Önce kullanıcının son mesajını doğalca cevapla.\n"
                f"Judge/score bu tur için şu eksik sinyale işaret ediyor: {recommended_next_question}\n"
                "Bu soruyu birebir kalıp gibi sorma; sohbetin içine tek kısa takip sorusu olarak yedir.\n"
                f"{segment_focus}\n"
            )

        return (
            "\n[BU TURDA NE YAPMALISIN]\n"
            "Önce kullanıcının son mesajını doğalca cevapla.\n"
            f"{segment_focus}\n"
            "Ana cevaptan sonra çoğu turda tek kısa takip sorusu yeterlidir.\n"
        )

    if recommended_next_question:
        return (
            "\n[WHAT TO DO THIS TURN]\n"
            "Answer the user's last message first.\n"
            f"The judge/score suggests this next gap: {recommended_next_question}\n"
            "Do not ask it verbatim; weave it naturally into one short follow-up.\n"
        )

    return (
        "\n[WHAT TO DO THIS TURN]\n"
        "Answer the user's last message first.\n"
        "Then move only one natural qualification step forward.\n"
    )


def _extract_lead_facts(lead_json: dict[str, Any], language: str) -> dict[str, str]:
    form_data = lead_json.get("form_data")
    form = form_data if isinstance(form_data, dict) else {}
    contact = lead_json.get("contact")
    contact_obj = contact if isinstance(contact, dict) else {}

    name = str(
        lead_json.get("name")
        or lead_json.get("full_name")
        or contact_obj.get("name")
        or form.get("full_name")
        or ""
    ).strip()
    _project_type_raw = _humanize_lead_value(
        _get_form_value(
            form,
            "what_type_of_property_are_you_interested_in?",
            "what_type_of_property_are_you_interested_in",
            "project_type",
        ) or str(lead_json.get("project_type") or ""),
        language,
    )
    # Simplify to general category (same as lead_context)
    _pt_lower = _project_type_raw.lower()
    if "villa" in _pt_lower:
        project_type = "villa"
    elif "penthouse" in _pt_lower:
        project_type = "penthouse"
    elif "stüdyo" in _pt_lower or "studio" in _pt_lower:
        project_type = "stüdyo daire"
    elif "daire" in _pt_lower or "apartment" in _pt_lower:
        project_type = "daire"
    else:
        project_type = _project_type_raw
    budget = _humanize_lead_value(
        _get_form_value(
            form,
            "what_is_your_budget_range?",
            "what_is_your_budget_range",
            "budget_range",
        ) or str(lead_json.get("budget_range") or ""),
        language,
    )
    purpose = _humanize_lead_value(
        _get_form_value(
            form,
            "why_are_you_interested_in_north_cyprus?",
            "why_are_you_interested_in_north_cyprus",
            "purpose",
        ) or str(lead_json.get("notes") or ""),
        language,
    )
    location = _humanize_lead_value(
        _get_form_value(form, "preferred_location", "city") or str(lead_json.get("city") or ""),
        language,
    )
    source = str(lead_json.get("source") or form.get("platform") or "").strip()

    return {
        "name": name,
        "project_type": project_type,
        "budget": budget,
        "purpose": purpose,
        "location": location,
        "source": source,
    }


def _build_lead_summary_section(lead_json: dict[str, Any], language: str) -> str:
    facts = _extract_lead_facts(lead_json, language)
    buyer_segment = _resolve_buyer_segment(lead_json, None, language)
    lines: list[str] = []

    if _is_turkish(language):
        lines.append(f"- Müşteri Tipi: {_localize_buyer_segment(buyer_segment, language)}")
        if facts["name"]:
            lines.append(f"- İsim: {facts['name']}")
        if facts["project_type"]:
            lines.append(f"- İlgi Alanı: {facts['project_type']}")
        if facts["budget"]:
            lines.append(f"- Bütçe Sinyali: {facts['budget']}")
        if facts["purpose"]:
            lines.append(f"- Amaç: {facts['purpose']}")
        if facts["location"]:
            lines.append(f"- Formdaki Lokasyon Sinyali: {facts['location']}")
        if facts["source"]:
            lines.append(f"- Kaynak: {facts['source']}")
        if not lines:
            lines.append("- Belirgin form özeti yok.")
        return "[LEAD ÖZETİ]\n" + "\n".join(lines)

    lines.append(f"- Buyer Segment: {_localize_buyer_segment(buyer_segment, language)}")
    if facts["name"]:
        lines.append(f"- Name: {facts['name']}")
    if facts["project_type"]:
        lines.append(f"- Interest: {facts['project_type']}")
    if facts["budget"]:
        lines.append(f"- Budget Signal: {facts['budget']}")
    if facts["purpose"]:
        lines.append(f"- Purpose: {facts['purpose']}")
    if facts["location"]:
        lines.append(f"- Location Signal: {facts['location']}")
    if facts["source"]:
        lines.append(f"- Source: {facts['source']}")
    if not lines:
        lines.append("- No clear lead summary.")
    return "[LEAD SUMMARY]\n" + "\n".join(lines)


def _compute_champ_gaps(
    champ_json: dict[str, Any] | None,
    language: str = "tr",
    lead_json: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> str:
    # Check what user already answered in conversation to avoid re-asking
    _conv_text = ""
    if messages:
        _conv_text = " ".join(
            str(m.get("content", "")).lower()
            for m in messages
            if str(m.get("role", "")).lower() == "user"
        )
    _already_said_airbnb = any(w in _conv_text for w in ("airbnb", "kısa dönem", "kisa donem"))
    _already_said_kira = any(w in _conv_text for w in ("kiralama", "kira getiri", "kira gelir"))
    _already_said_timing = any(w in _conv_text for w in ("bu yaz", "bu yıl", "birkaç ay", "yakında", "hemen"))

    # If judge provided a recommended next question, wrap with naturalness guard
    if champ_json and champ_json.get("recommended_next_question"):
        rnq = champ_json["recommended_next_question"]
        # Skip investment model question if user already answered
        if _already_said_airbnb or _already_said_kira:
            if any(w in rnq.lower() for w in ("erken al", "uzun vadeli", "kısa dönem", "airbnb", "hibrit", "yatırım model")):
                rnq = "Müşteri zaten Airbnb/kiralama modelini belirtti. Bu soruyu TEKRAR SORMA. Sonraki eksik bilgiye geç (bütçe, zamanlama veya karar verici)."
        if _already_said_timing:
            if any(w in rnq.lower() for w in ("ne zaman", "zamanlama", "zaman çerçeve")):
                rnq = "Müşteri zaten zamanlama verdi. Bu soruyu TEKRAR SORMA. Sonraki eksik bilgiye geç."
        if _is_turkish(language):
            return f"Önerilen soru: {rnq}\nBu soruyu DOĞRUDAN sorma — doğal sohbet akışı içinde sor."
        return f"Suggested question: {rnq}\nDo NOT ask this directly — weave it naturally into the conversation."

    hints = _GAP_HINTS.get(language, _GAP_HINTS["en"])

    if not champ_json:
        lead_facts = _extract_lead_facts(lead_json or {}, language) if lead_json else {}
        has_purpose = bool(lead_facts.get("purpose"))
        has_budget = bool(lead_facts.get("budget"))
        has_location = bool(lead_facts.get("location"))
        buyer_segment = _resolve_buyer_segment(lead_json or {}, None, language)

        if _is_turkish(language):
            if has_purpose and buyer_segment == "investor":
                return (
                    "Formda yatırımcı sinyali var. Aynı şeyi tekrar segment sorusu gibi açma.\n"
                    "Öncelik: yatırım modelini (erken al-sat, kira, Airbnb veya hibrit), finansman hazırlığını ve zamanlamayı doğal şekilde netleştir.\n"
                )
            if has_purpose and buyer_segment == "holiday_home":
                return (
                    "Formda tatil evi sinyali var. Aynı amacı tekrar sorma.\n"
                    "Öncelik: Kıbrıs bilgisi, kullanım dönemi ve satın alma zamanını doğal şekilde netleştir.\n"
                )
            if has_purpose and buyer_segment == "residence":
                return (
                    "Formda yaşam / oturum sinyali var. Aynı amacı tekrar sorma.\n"
                    "Öncelik: taşınma tarihi, günlük yaşam ihtiyaçları ve karar sürecini doğal şekilde netleştir.\n"
                )
            if has_purpose and not has_budget:
                return (
                    "Formda amaç sinyali var. Aynı amacı tekrar sorma.\n"
                    "Öncelik: önce müşterinin Kıbrıs'a ne kadar hakim olduğunu veya zamanlamasını doğal şekilde netleştir.\n"
                )
            if has_purpose and has_budget and not has_location:
                return (
                    "Formda amaç ve bütçe sinyali var. Bunları tekrar etme.\n"
                    "Öncelik: önce Kıbrıs bilgisi / genel yönü anla, sonra zamanlama ve karar sürecine geç.\n"
                )
        return hints["all_missing"]

    gaps = {
        "challenges": champ_json.get("challenges_score", 0),
        "authority": champ_json.get("authority_score", 0),
        "money": champ_json.get("money_score", 0),
        "prioritization": champ_json.get("prioritization_score", 0),
    }

    # Find ALL critical gaps (score < 10) — not just the single lowest
    critical_threshold = 10
    critical_gaps = {k: v for k, v in gaps.items() if v < critical_threshold}

    if len(critical_gaps) >= 3:
        # Most dimensions missing — prioritize by natural conversation order
        priority = ["challenges", "money", "prioritization", "authority"]
        first = next(d for d in priority if d in critical_gaps)
        hint = hints.get(first, "")
        if _is_turkish(language):
            others = [d for d in priority if d in critical_gaps and d != first]
            return (
                f"Eksik alan: {first} ({critical_gaps[first]}/25). {hint}\n"
                f"Diğer eksikler: {', '.join(others)}. Hepsini bir anda sorma — doğal akışta tek tek topla."
            )
        others = [d for d in priority if d in critical_gaps and d != first]
        return (
            f"Biggest gap: {first} (score: {critical_gaps[first]}/25). {hint}\n"
            f"Also missing: {', '.join(others)}. Don't ask all at once — gather naturally."
        )

    if len(critical_gaps) == 2:
        dims = sorted(critical_gaps, key=critical_gaps.get)  # type: ignore[arg-type]
        hint1 = hints.get(dims[0], "")
        hint2 = hints.get(dims[1], "")
        if _is_turkish(language):
            return (
                f"İki eksik alan var:\n"
                f"1. {dims[0]} ({critical_gaps[dims[0]]}/25): {hint1}\n"
                f"2. {dims[1]} ({critical_gaps[dims[1]]}/25): {hint2}\n"
                f"Önce birincisini sor, ikincisini sonraki mesajlara bırak."
            )
        return (
            f"Two gaps:\n"
            f"1. {dims[0]} (score: {critical_gaps[dims[0]]}/25): {hint1}\n"
            f"2. {dims[1]} (score: {critical_gaps[dims[1]]}/25): {hint2}\n"
            f"Ask the first one now, save the second for later."
        )

    # Single biggest gap or all dimensions reasonably covered
    biggest_gap_dim = min(gaps, key=gaps.get)  # type: ignore[arg-type]
    biggest_gap_score = gaps[biggest_gap_dim]

    hint = hints.get(biggest_gap_dim, "")
    return hints["prefix"].format(dim=biggest_gap_dim, score=biggest_gap_score, hint=hint)


# ── Chat system prompt ───────────────────────────────────────────────────────

def build_chat_system_prompt(
    lead_json: dict[str, Any],
    champ_json: dict[str, Any] | None = None,
    company_config: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> str:
    cfg = company_config or {}
    lead_json = _apply_buyer_segment_hint(lead_json, cfg.get("buyer_segment"))
    language = _resolve_language(cfg)
    sector = _resolve_sector(cfg)
    turkish = _is_turkish(language)
    buyer_segment = _resolve_buyer_segment(lead_json, messages, language)

    # Tone instruction
    tone = cfg.get("tone") or "professional"
    tone_map = _TONE_MAP_TR if turkish else _TONE_MAP_EN
    tone_instruction = tone_map.get(tone, tone_map["professional"])
    brand_voice_section = _build_brand_voice_section(cfg, language, tone_instruction)
    sales_playbook_section = _build_chat_sales_playbook_section(cfg, language)

    # Working hours
    working_hours = cfg.get("working_hours") or ""
    working_hours_section = ""
    if working_hours:
        if turkish:
            working_hours_section = (
                f"\n[ÇALIŞMA SAATLERİ]\nŞirket çalışma saatleri: {working_hours}."
                " Müşteri özellikle zaman ısrarı yaparsa bu saatleri referans al; aksi halde tarih-saat pazarlığına girme.\n"
            )
        else:
            working_hours_section = (
                f"\n[WORKING HOURS]\nCompany working hours: {working_hours}."
                " Only use these if the customer explicitly insists on timing details."
                " Otherwise avoid negotiating schedule details in chat."
                "\n"
            )

    # Pricing hints
    pricing_hints = cfg.get("pricing_hints") or ""
    pricing_hints_section = ""
    if pricing_hints:
        if turkish:
            pricing_hints_section = f"\n[FİYAT İPUÇLARI - SADECE REFERANS]\n{pricing_hints}\nBu bilgiyi müşteriye doğrudan paylaşma, sadece sohbeti yönlendirmek için kullan.\n"
        else:
            pricing_hints_section = f"\n[PRICING HINTS - REFERENCE ONLY]\n{pricing_hints}\nDo not share this directly with the customer, use it only to guide the conversation.\n"

    # KB documents content
    kb_content = cfg.get("kb_documents_content") or ""
    kb_section = ""
    if kb_content:
        if turkish:
            kb_section = f"\n[ŞİRKET BİLGİ BANKASI - DÖKÜMANLAR]\n{kb_content}\n"
        else:
            kb_section = f"\n[COMPANY KNOWLEDGE BASE - DOCUMENTS]\n{kb_content}\n"
    turn_guidance_section = _build_turn_guidance_section(messages, language, lead_json, champ_json)
    lead_summary_section = _build_lead_summary_section(lead_json, language)
    buyer_segment_section = _build_buyer_segment_section(buyer_segment, language)
    matched_projects_section = ""
    raw_matched_projects_section = cfg.get("matched_projects_section") or ""
    recent_user_window = _collect_recent_user_text(messages, limit=4).lower()
    project_request_keywords = (
        # Doğrudan proje/seçenek isteme
        "neler var",
        "ne var",
        "ne öner",
        "ne oner",
        "ne önerirsiniz",
        "ne onerirsiniz",
        "öneri",
        "oneri",
        "seçenek",
        "secenek",
        "proje",
        "portföy",
        "portfoly",
        "listele",
        "göster",
        "goster",
        "gönder",
        "gonder",
        "var mı projeniz",
        "var mi projeniz",
        "projeniz var mı",
        "projeniz var mi",
        "uygun proje",
        "site sizin mi",
        "proje sizin mi",
        "size ait mi",
        "sizin mi",
        "ait mi",
        "studio",
        "stüdyo",
        "properties",
        "hangi üniteler",
        "hangi uniteler",
        "ünite tipleri",
        "secenekler ne",
        "seçenekler ne",
        "imkanları ne",
        "imkanlari ne",
        "tesisleri",
        "tesisler",
        "link var mı",
        "link var mi",
        # Detay / fiyat
        "detay",
        "detaylı",
        "fiyat",
        "ödeme",
        "odeme",
        "teslim",
        "stok",
        "hangi proje",
        "hangi ev",
        "hangisi",
        "broşür",
        "brosur",
        "pdf",
        # Yatırım / getiri
        "kira getirisi",
        "getiri",
        "nasıl yatırım",
        "nasil yatirim",
    )
    project_request_keywords_by_segment = {
        "investor": project_request_keywords,
        "holiday_home": (
            "seçenek",
            "secenek",
            "proje",
            "var mı projeniz",
            "var mi projeniz",
            "projeniz var mı",
            "projeniz var mi",
            "detay",
            "detaylı",
            "hangi proje",
            "hangi ev",
            "site sizin mi",
            "size ait mi",
            "sizin mi",
            "studio",
            "stüdyo",
            "properties",
            "hangi üniteler",
            "hangi uniteler",
            "ünite tipleri",
            "seçenekler ne",
            "secenekler ne",
            "imkanları ne",
            "imkanlari ne",
            "tesisleri",
            "link var mı",
            "link var mi",
            "broşür",
            "brosur",
            "pdf",
            "fiyat",
            "öneri",
            "oneri",
            "göster",
            "goster",
            "gönder",
            "gonder",
        ),
        "residence": (
            "seçenek",
            "secenek",
            "proje",
            "var mı projeniz",
            "var mi projeniz",
            "hangi proje",
            "hangi ev",
            "detay",
            "detaylı",
            "fiyat",
            "uygun seçenek",
            "uygun proje",
            "site sizin mi",
            "size ait mi",
            "sizin mi",
            "properties",
            "hangi üniteler",
            "hangi uniteler",
            "ünite tipleri",
            "seçenekler ne",
            "secenekler ne",
            "imkanları ne",
            "imkanlari ne",
            "tesisleri",
            "link var mı",
            "link var mi",
            "ne öner",
            "ne oner",
            "öneri",
            "oneri",
            "göster",
            "goster",
            "gönder",
            "gonder",
        ),
        "generic": project_request_keywords,
    }
    should_show_matched_projects = False
    if raw_matched_projects_section:
        if not messages:
            should_show_matched_projects = False
        else:
            # Collect ALL consecutive user messages since last assistant reply (burst-aware)
            last_user_message = ""
            user_burst_parts: list[str] = []
            for message in reversed(messages):
                role = str(message.get("role", "")).lower()
                if role == "user":
                    text = str(message.get("content", "")).lower().strip()
                    if not last_user_message:
                        last_user_message = text
                    user_burst_parts.insert(0, text)
                elif role == "assistant":
                    break
            last_user_message = " ".join(user_burst_parts) if user_burst_parts else last_user_message

            # Also check recent conversation window (last 4 user messages across turns)
            # so KB stays visible during active project discussions
            recent_user_msgs: list[str] = []
            for message in reversed(messages):
                if str(message.get("role", "")).lower() == "user" and message.get("content"):
                    recent_user_msgs.insert(0, str(message["content"]).lower().strip())
                    if len(recent_user_msgs) >= 4:
                        break
            recent_user_window_text = " ".join(recent_user_msgs)

            # Continuation keywords — indicate user is still in project/area discussion
            _continuation_keywords = (
                "başka", "baska", "alternatif", "daha uygun", "daha ucuz",
                "yok mu", "bu kadar mı", "bu kadar mi",
                "daha var mı", "daha var mi", "farklı", "farkli",
                "birkaç", "birkac", "kaç tane", "kac tane", "kaçar", "kacar",
                # Area/location keywords — triggers KB for area knowledge
                "esentepe", "çatalköy", "catalkoy", "girne", "kyrenia",
                "bölge", "bolge", "lokasyon", "nerede", "nasıl bir yer",
                "mesafe", "uzaklık", "uzaklik", "havalimanı", "havalimani",
                "denize ne kadar", "sahil", "plaj",
            )

            keywords = project_request_keywords_by_segment.get(buyer_segment, project_request_keywords)
            # Match on current burst OR recent window (keeps KB alive during project chat)
            should_show_matched_projects = (
                any(keyword in last_user_message for keyword in keywords)
                or any(keyword in last_user_message for keyword in _continuation_keywords)
                or any(keyword in recent_user_window_text for keyword in keywords)
            )
            explicit_detail_keywords = (
                "proje",
                "properties",
                "hangi üniteler",
                "hangi uniteler",
                "ünite tipleri",
                "seçenekler ne",
                "secenekler ne",
                "fiyat",
                "imkan",
                "tesis",
                "link",
                "broşür",
                "brosur",
                "pdf",
                "resim",
                "görsel",
                "gorsel",
                "foto",
                "render",
                "site sizin mi",
                "sizin mi",
                "size ait mi",
            )
            early_exploration_tokens = (
                "bakınıyorum",
                "bakiniyorum",
                "araştırıyorum",
                "arastiriyorum",
                "değerlendiriyorum",
                "degerlendiriyorum",
                "daha bilgim yok",
                "fikrim yok",
                "henüz",
                "henuz",
                "karar vermedim",
            )

            # Also trigger if user mentions any known project name (active or reference)
            if not should_show_matched_projects and last_user_message:
                known_names = cfg.get("known_project_names") or []
                import re as _re
                name_tokens: set[str] = set()
                for _n in known_names:
                    low = _n.lower()
                    name_tokens.add(low)
                    # Base name without trailing phase number
                    base = _re.sub(r"\s+\d+$", "", _n).strip().lower()
                    if base and len(base) >= 4:
                        name_tokens.add(base)
                    # Distinctive words (5+ chars) as standalone triggers
                    for word in _re.split(r"[\s&]+", low):
                        if len(word) >= 5 and word not in (
                            "homes", "villas", "beach", "resort", "health",
                            "wellness", "island",
                        ):
                            name_tokens.add(word)
                for tok in name_tokens:
                    if tok in last_user_message:
                        should_show_matched_projects = True
                        break
            if should_show_matched_projects:
                has_explicit_detail_intent = any(
                    keyword in last_user_message for keyword in explicit_detail_keywords
                )
                is_still_exploring = any(
                    token in recent_user_window for token in early_exploration_tokens
                )
                if is_still_exploring and not has_explicit_detail_intent:
                    should_show_matched_projects = False
    if should_show_matched_projects:
        matched_projects_section = raw_matched_projects_section
        # kb_section stays — gives AI full portfolio context alongside match
    else:
        kb_section = ""
    visible_verified_knowledge = matched_projects_section or kb_section
    knowledge_guard_section = _build_knowledge_guard_section(language, visible_verified_knowledge)

    templates = TemplateRegistry.get_templates(language, sector)

    lead_context = json.dumps(_build_prompt_lead_context(lead_json, language), ensure_ascii=False, indent=2)

    champ_section = ""
    if champ_json:
        champ_context = json.dumps(champ_json, ensure_ascii=False, indent=2)
        if turkish:
            champ_section = f"\n\nMevcut CHAMP Analizi (JSON):\n{champ_context}\n"
        else:
            champ_section = f"\n\nCurrent CHAMP Analysis (JSON):\n{champ_context}\n"
        # Inject holistic reasoning from judge (if available)
        if champ_json.get("holistic_reasoning"):
            label = "Kalifikasyon Analizi" if turkish else "Qualification Analysis"
            champ_section += f"\n{label}: {champ_json['holistic_reasoning']}\n"
        if champ_json.get("icp_fit_assessment"):
            label = "ICP Uyumu" if turkish else "ICP Fit"
            champ_section += f"{label}: {champ_json['icp_fit_assessment']}\n"

    # Company customization
    company_name = cfg.get("company_display_name") or ""
    industry = _localize_sector_name(sector, language)
    assistant_name = _resolve_assistant_name(cfg, language)
    persona = cfg.get("custom_persona") or (
        "10 yıllık deneyime sahip kıdemli yatırım ve proje danışmanı"
        if turkish
        else "Senior Investment and Project Advisor with 10 years of expertise"
    )
    company_context = (
        f"{company_name} ekibinde, "
        if company_name and turkish
        else f" representing {company_name}"
        if company_name
        else ""
    )
    persona_instruction_section = (
        "\nROL NOTU:\n"
        f"- {persona} gibi düşün ve bu uzmanlık seviyesinde konuş.\n"
        "- Bunu yaparken kısa, doğal ve insan gibi kal.\n"
        if turkish
        else "\nPERSONA NOTE:\n"
        f"- Sound like {persona}.\n"
        "- Keep that expertise while still writing like a real person.\n"
    )

    # Forbidden topics
    forbidden = _resolve_playbook_list(cfg, "forbidden_topics", language)
    forbidden_section = ""
    if forbidden:
        forbidden_items = "\n".join(f"- {t}" for t in forbidden)
        header = "EK YASAK KONULAR" if turkish else "ADDITIONAL FORBIDDEN TOPICS"
        forbidden_section = f"\n[{header}]\n{forbidden_items}\n"

    # FAQ
    faq = _resolve_playbook_faq(cfg, language)
    faq_section = ""
    if faq:
        faq_items = "\n".join(
            f"Q: {f.get('question', '')} A: {f.get('answer', '')}"
            for f in faq
            if isinstance(f, dict)
        )
        if faq_items:
            header = "ŞİRKET BİLGİ BANKASI - SSS" if turkish else "COMPANY KNOWLEDGE BASE"
            faq_section = f"\n[{header}]\n{faq_items}\n"

    # Custom qualifying questions
    custom_qs = _resolve_playbook_list(cfg, "custom_qualifying_questions", language)
    custom_qs_section = ""
    if custom_qs:
        qs_items = "\n".join(f"- {q}" for q in custom_qs)
        header = "ÖNCELİKLİ KALİFİKASYON SORULARI" if turkish else "PRIORITY QUALIFYING QUESTIONS"
        custom_qs_section = f"\n[{header}]\n{qs_items}\n"

    # Gap-aware instruction
    champ_gap_instruction = _compute_champ_gaps(champ_json, language, lead_json, messages)

    return templates.chat_system.format(
        persona=persona,
        assistant_name=assistant_name,
        company_context=company_context,
        industry=industry,
        buyer_segment_section=buyer_segment_section,
        lead_context=lead_context,
        champ_section=champ_section,
        champ_gap_instruction=champ_gap_instruction,
        forbidden_section=forbidden_section,
        faq_section=faq_section,
        custom_qs_section=custom_qs_section,
        tone_instruction=tone_instruction,
        brand_voice_section=brand_voice_section,
        sales_playbook_section=sales_playbook_section,
        persona_instruction_section=persona_instruction_section,
        turn_guidance_section=turn_guidance_section,
        lead_summary_section=lead_summary_section,
        working_hours_section=working_hours_section,
        pricing_hints_section=pricing_hints_section,
        knowledge_guard_section=knowledge_guard_section,
        kb_section=kb_section,
        matched_projects_section=matched_projects_section,
    )


# ── Handoff closing prompt ───────────────────────────────────────────────────

def build_handoff_closing_prompt(
    company_config: dict[str, Any] | None = None,
    lead_json: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
    cta_type: str = "cyprus_visit",
    meeting_url: str | None = None,
) -> str:
    cfg = company_config or {}
    language = cfg.get("primary_language") or "tr"
    sector = cfg.get("industry_focus") or "construction"

    templates = TemplateRegistry.get_templates(language, sector)

    # Select closing variant by CTA type.
    cta_norm = (cta_type or "cyprus_visit").lower().strip()
    if cta_norm == "calendly":
        template = templates.closing_calendly or templates.closing
    elif cta_norm == "nurture":
        template = templates.closing_nurture or templates.closing
    else:  # cyprus_visit / default
        template = templates.closing_visit or templates.closing

    # Extract customer name
    customer_name = ""
    if lead_json:
        customer_name = lead_json.get("name") or lead_json.get("full_name") or ""

    # Build recent topics summary from last few messages
    recent_topics = ""
    if messages:
        recent_msgs = [
            m["content"] for m in messages[-6:]
            if m.get("content")
        ]
        recent_topics = " | ".join(recent_msgs)
        if len(recent_topics) > 500:
            recent_topics = recent_topics[:500]

    # Calendly variant requires meeting_url; fall back to a generic placeholder
    # that the LLM can still reason about (never render empty).
    resolved_meeting_url = meeting_url or ""

    format_kwargs = {
        "customer_name": customer_name or "(bilinmiyor)",
        "recent_topics": recent_topics or "(sohbet özeti yok)",
        "meeting_url": resolved_meeting_url,
    }

    try:
        return template.format(**format_kwargs)
    except (KeyError, IndexError):
        # Fallback for templates without placeholders
        return template


# ── CHAMP extraction prompt ──────────────────────────────────────────────────

def build_champ_extraction_prompt(
    conversation_history: str,
    current_champ_json: dict[str, Any] | None = None,
    company_config: dict[str, Any] | None = None,
    language: str | None = None,
    sector: str | None = None,
) -> str:
    cfg = company_config or {}
    language = _resolve_language(cfg, language)
    sector = _resolve_sector(cfg, sector)
    turkish = _is_turkish(language)

    from app.domain.conversation.few_shots.registry import FewShotRegistry

    templates = TemplateRegistry.get_templates(language, sector)
    few_shots = FewShotRegistry.get_examples(language, sector)

    current_section = ""
    if current_champ_json:
        heading = "## Mevcut CHAMP Durumu" if turkish else "## Current CHAMP State"
        note = (
            "SADECE yeni bilgi gelen boyutları güncelle. "
            "Değişmeyen boyutları mevcut skorlarında bırak.\n"
            if turkish
            else "Only update dimensions where new information was provided. "
            "Keep unchanged dimensions at their current scores.\n"
        )
        current_section = (
            f"{heading}\n"
            f"```json\n{json.dumps(current_champ_json, ensure_ascii=False, indent=2)}\n```\n"
            f"{note}"
        )

    prompt = templates.extraction.format(
        conversation_history=conversation_history,
        current_champ_section=current_section,
        sector_qualifiers_instruction=templates.extraction_sector_instruction,
    )

    if few_shots:
        prompt = few_shots + "\n\n" + prompt

    return prompt


# ── Qualification judge prompt ────────────────────────────────────────────────

def build_qualification_judge_prompt(
    conversation_history: str,
    lead_json: dict[str, Any],
    current_judgment_json: dict[str, Any] | None = None,
    company_config: dict[str, Any] | None = None,
    language: str | None = None,
    sector: str | None = None,
) -> str:
    """Build the qualification judge system prompt with ICP, few-shots, current state."""
    cfg = company_config or {}
    lead_json = _apply_buyer_segment_hint(lead_json, cfg.get("buyer_segment"))
    language = _resolve_language(cfg, language)
    sector = _resolve_sector(cfg, sector)

    from app.domain.conversation.few_shots.registry import FewShotRegistry

    templates = TemplateRegistry.get_templates(language, sector)
    if not templates.qualification_judge:
        raise ValueError(f"No qualification judge template for language={language}, sector={sector}")

    few_shots = FewShotRegistry.get_judge_examples(language, sector)

    # ICP: company-defined or default
    if _is_turkish(language):
        from app.domain.conversation.templates.tr.qualification_judge import DEFAULT_ICP_TR
        default_icp = DEFAULT_ICP_TR
    else:
        from app.domain.conversation.templates.en.qualification_judge import DEFAULT_ICP_EN
        default_icp = DEFAULT_ICP_EN

    icp = _resolve_ideal_customer_profile(cfg, language, default_icp)

    # Company context
    company_name = cfg.get("company_display_name") or ""
    industry = _localize_sector_name(sector, language)
    company_context = f"{company_name} — {industry}" if company_name else industry

    # Current judgment state
    current_section = ""
    if current_judgment_json:
        current_section = (
            "## Mevcut Değerlendirme\n"
            f"```json\n{json.dumps(current_judgment_json, ensure_ascii=False, indent=2)}\n```\n"
            "SADECE yeni bilgi eklenen boyutları güncelle. "
            "Değişmeyen boyutları mevcut skorlarında bırak.\n"
        ) if _is_turkish(language) else (
            "## Current Assessment\n"
            f"```json\n{json.dumps(current_judgment_json, ensure_ascii=False, indent=2)}\n```\n"
            "Only update dimensions where new information was provided. "
            "Keep unchanged dimensions at their current scores.\n"
        )

    # Few-shot section
    few_shot_section = f"\n{few_shots}" if few_shots else ""

    buyer_segment = _resolve_buyer_segment(lead_json, None, language)
    buyer_segment_section = _build_judge_buyer_segment_section(buyer_segment, language)
    sales_playbook_section = _build_judge_sales_playbook_section(cfg, language)
    lead_context = json.dumps(lead_json, ensure_ascii=False, indent=2)

    return templates.qualification_judge.format(
        sector=industry,
        ideal_customer_profile=icp,
        company_context=company_context,
        buyer_segment_section=buyer_segment_section,
        sales_playbook_section=sales_playbook_section,
        lead_json=lead_context,
        conversation_history=conversation_history,
        current_judgment_section=current_section,
        few_shot_section=few_shot_section,
    )


# ── Reasoning report prompt ──────────────────────────────────────────────────

def build_reasoning_report_prompt(
    lead_json: dict[str, Any],
    score: int,
    score_breakdown: dict[str, int],
    champ_json: dict[str, Any] | None = None,
    company_config: dict[str, Any] | None = None,
) -> str:
    cfg = company_config or {}
    language = cfg.get("primary_language") or "tr"
    sector = cfg.get("industry_focus") or "construction"

    templates = TemplateRegistry.get_templates(language, sector)

    lead_context = json.dumps(lead_json, ensure_ascii=False, indent=2)
    breakdown_context = json.dumps(score_breakdown, ensure_ascii=False, indent=2)

    champ_section = ""
    if champ_json:
        champ_section = f"\n## CHAMP Analysis\n```json\n{json.dumps(champ_json, ensure_ascii=False, indent=2)}\n```"

    return templates.reasoning.format(
        industry=sector,
        lead_context=lead_context,
        score=score,
        breakdown_context=breakdown_context,
        champ_section=champ_section,
    )
