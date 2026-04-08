"""
Generic prompt builders — delegates to language/sector-specific templates.

All functions are pure string builders. No I/O.
"""
import json
from typing import Any

from app.domain.conversation.templates.registry import TemplateRegistry


# ── Gap-aware CHAMP routing ──────────────────────────────────────────────────

_GAP_HINTS = {
    "tr": {
        "all_missing": "Tüm boyutlar eksik. Öncelik: ne tür mülk arıyorlar?",
        "challenges": "Ne tür mülk düşünüyor? Tercihlerini anlamaya çalış.",
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


def _is_turkish(language: str) -> bool:
    return language.lower().strip() in ("tr", "turkish")


def _resolve_language(cfg: dict[str, Any], language: str | None = None) -> str:
    return (language or cfg.get("primary_language") or "tr").lower().strip()


def _resolve_sector(cfg: dict[str, Any], sector: str | None = None) -> str:
    return sector or cfg.get("industry_focus") or "construction"


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


def _compute_champ_gaps(champ_json: dict[str, Any] | None, language: str = "tr") -> str:
    # If judge provided a recommended next question, wrap with naturalness guard
    if champ_json and champ_json.get("recommended_next_question"):
        rnq = champ_json["recommended_next_question"]
        if _is_turkish(language):
            return f"Önerilen soru: {rnq}\nBu soruyu DOĞRUDAN sorma — doğal sohbet akışı içinde sor."
        return f"Suggested question: {rnq}\nDo NOT ask this directly — weave it naturally into the conversation."

    hints = _GAP_HINTS.get(language, _GAP_HINTS["en"])

    if not champ_json:
        return hints["all_missing"]

    gaps = {
        "challenges": champ_json.get("challenges_score", 0),
        "authority": champ_json.get("authority_score", 0),
        "money": champ_json.get("money_score", 0),
        "prioritization": champ_json.get("prioritization_score", 0),
    }

    # Find the dimension with the lowest score
    biggest_gap_dim = min(gaps, key=gaps.get)  # type: ignore[arg-type]
    biggest_gap_score = gaps[biggest_gap_dim]

    hint = hints.get(biggest_gap_dim, "")
    return hints["prefix"].format(dim=biggest_gap_dim, score=biggest_gap_score, hint=hint)


# ── Chat system prompt ───────────────────────────────────────────────────────

def build_chat_system_prompt(
    lead_json: dict[str, Any],
    champ_json: dict[str, Any] | None = None,
    company_config: dict[str, Any] | None = None,
) -> str:
    cfg = company_config or {}
    language = _resolve_language(cfg)
    sector = _resolve_sector(cfg)
    turkish = _is_turkish(language)

    # Tone instruction
    tone = cfg.get("tone") or "professional"
    tone_map = _TONE_MAP_TR if turkish else _TONE_MAP_EN
    tone_instruction = tone_map.get(tone, tone_map["professional"])

    # Working hours
    working_hours = cfg.get("working_hours") or ""
    working_hours_section = ""
    if working_hours:
        if turkish:
            working_hours_section = (
                f"\n[ÇALIŞMA SAATLERİ]\nŞirket çalışma saatleri: {working_hours}."
                " Müşteri randevu veya görüşme zamanı sorarsa bu saatleri referans al.\n"
            )
        else:
            working_hours_section = (
                f"\n[WORKING HOURS]\nCompany working hours: {working_hours}."
                " Reference these hours when the customer asks about availability"
                " or meeting times.\n"
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
    knowledge_guard_section = _build_knowledge_guard_section(language, kb_content)

    templates = TemplateRegistry.get_templates(language, sector)

    lead_context = json.dumps(lead_json, ensure_ascii=False, indent=2)

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
    forbidden = cfg.get("forbidden_topics") or []
    forbidden_section = ""
    if forbidden:
        forbidden_items = "\n".join(f"- {t}" for t in forbidden)
        header = "EK YASAK KONULAR" if turkish else "ADDITIONAL FORBIDDEN TOPICS"
        forbidden_section = f"\n[{header}]\n{forbidden_items}\n"

    # FAQ
    faq = cfg.get("faq_entries") or []
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
    custom_qs = cfg.get("custom_qualifying_questions") or []
    custom_qs_section = ""
    if custom_qs:
        qs_items = "\n".join(f"- {q}" for q in custom_qs)
        header = "ÖNCELİKLİ KALİFİKASYON SORULARI" if turkish else "PRIORITY QUALIFYING QUESTIONS"
        custom_qs_section = f"\n[{header}]\n{qs_items}\n"

    # Gap-aware instruction
    champ_gap_instruction = _compute_champ_gaps(champ_json, language)

    return templates.chat_system.format(
        persona=persona,
        company_context=company_context,
        industry=industry,
        lead_context=lead_context,
        champ_section=champ_section,
        champ_gap_instruction=champ_gap_instruction,
        forbidden_section=forbidden_section,
        faq_section=faq_section,
        custom_qs_section=custom_qs_section,
        tone_instruction=tone_instruction,
        persona_instruction_section=persona_instruction_section,
        working_hours_section=working_hours_section,
        pricing_hints_section=pricing_hints_section,
        knowledge_guard_section=knowledge_guard_section,
        kb_section=kb_section,
    )


# ── Handoff closing prompt ───────────────────────────────────────────────────

def build_handoff_closing_prompt(company_config: dict[str, Any] | None = None) -> str:
    cfg = company_config or {}
    language = cfg.get("primary_language") or "tr"
    sector = cfg.get("industry_focus") or "construction"

    templates = TemplateRegistry.get_templates(language, sector)
    return templates.closing


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

    icp = cfg.get("ideal_customer_profile") or default_icp

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

    lead_context = json.dumps(lead_json, ensure_ascii=False, indent=2)

    return templates.qualification_judge.format(
        sector=industry,
        ideal_customer_profile=icp,
        company_context=company_context,
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
