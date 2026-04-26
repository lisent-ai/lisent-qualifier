"""PreScoreJudgmentResult — extractive LLM judgment schema.

2026 best practice: LLM extracts structured enum-typed signals; deterministic
composer (composer.py) turns them into a 0-100 score. This split is crucial
because direct-score prompts drift 15-30% with prompt phrasing (bias research).

Field order matters (anti-bias):
    thinking → identity → intent → fit → risk → sales_context → extraction_confidence

Score fields are ABSENT from the LLM output by design — LLM only chooses enum
values from calibrated lists. `composer.compose_pre_score()` applies the
deterministic formula.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IdentitySignals(BaseModel):
    """Kim oldukları hakkında sinyaller — form + OSINT kombine."""

    name_quality: Literal[
        "missing",     # isim hiç yok
        "random",      # "aaa", "test", "xxx"
        "weak",        # tek harfli, eksik soyisim
        "plausible",   # isim + soyisim muhtemel gerçek
        "strong",      # gerçek isim + şirket eşleşmesi doğrulanmış
    ]
    email_domain_class: Literal[
        "missing",               # email hiç yok
        "disposable",            # curated blocklist'ten: mailinator, 10minutemail, temp-mail.org, vb. (gerçek fraud sinyali)
        "public_provider",       # gmail, outlook, yahoo, icloud, yandex, protonmail — NEUTRAL. Türkiye'de en yaygın kullanılan servisler; B2B için negatif sinyal DEĞİL
        "generic_tld",           # .com/.net/.org vb. genel TLD, public_provider değil (custom domain)
        "country_tld",           # .com.tr/.co.uk/.de vb. ülke TLD'si — belirli bir pazarda aktif işletme sinyali
        "registry_tld",          # .gov.tr/.edu.tr/.gov/.edu — resmi kurumsal tescil (objektif doğrulanmış)
        # Legacy geçiş döneminde eski değerler de Literal'de yer alabilir —
        # Pydantic validation'ı kırmamak için birkaç deploy cycle tutuluyor
        "freemail",              # DEPRECATED: public_provider kullan
        "corporate_suspected",   # DEPRECATED: generic_tld / country_tld kullan
        "corporate_verified",    # DEPRECATED: registry_tld kullan
    ]
    phone_validity: Literal[
        "missing",
        "invalid_format",        # 5555..., test numarası, formatsız
        "valid_format",          # PhoneInfoga country + e164 parse etti
        "verified_reachable",    # future: numverify/Twilio reachability
    ]
    osint_digital_footprint: Literal[
        "none",     # OSINT.email.site_count = 0
        "low",      # 1-4
        "medium",   # 5-14
        "high",     # 15+
    ]
    evidence: list[str] = Field(default_factory=list, max_length=6)


class IntentSignals(BaseModel):
    """Ne istediklerine dair sinyaller — notes + raw_payload'dan."""

    project_specificity: Literal[
        "none",        # hiçbir proje detayı yok
        "vague",       # "ev bakıyorum", "villa istiyorum"
        "described",   # şehir + proje tipi + yaklaşık büyüklük
        "detailed",    # şehir + semt + m² + bütçe + zamanlama hepsi var
    ]
    budget_signal: Literal[
        "absent",           # bütçe bilgisi yok
        "range_stated",     # "10-15M TL", "500K-1M"
        "specific_amount",  # "14M TL", "800.000 USD"
    ]
    timeline_signal: Literal[
        "absent",
        "exploratory",         # "merak", "bilgi almak"
        "short_term_soft",     # "önümüzdeki aylar", "yakında"
        "committed_timeline",  # "Eylül başlamak", "Q3 teslim"
    ]
    authority_signal: Literal[
        "absent",
        "influencer",      # "ailemle karar vereceğiz", "yönetime sunacağım"
        "joint_decider",   # "ortağımla birlikte", "eşimle karar veriyoruz"
        "sole_decider",    # "ben karar veriyorum", "ben sahipim"
    ]
    buying_stage: Literal[
        "curious_browsing",
        "researching_options",
        "actively_evaluating",
        "ready_to_engage",
    ]
    urgency_cues: list[str] = Field(default_factory=list, max_length=4)
    evidence: list[str] = Field(default_factory=list, max_length=6)


class FitSignals(BaseModel):
    """Tenant'ın ICP'sine uyum — tenant.config'deki ICP tanımına göre."""

    icp_alignment: Literal[
        "unknown",          # yetersiz bilgi — karar verilemedi (diğer fit alanlarıyla tutarlı)
        "off_icp",          # hedef kitlenin açıkça dışında
        "edge_case",        # belirsiz, sinyal var ama zayıf
        "partial_match",    # bir iki kriter eşleşiyor
        "close_match",      # çoğu kriter eşleşiyor
        "ideal_match",      # tam ICP'de
    ]
    project_type_in_tenant_scope: Literal["unknown", "off_scope", "adjacent", "in_scope"]
    geography_in_scope: Literal["unknown", "outside", "serviceable", "core_market"]
    company_size_fit: Literal["unknown", "too_small", "fit", "large_enterprise"]
    segment_label: str = Field(
        default="", max_length=60,
        description=(
            "Satış ekibi için 2-4 kelimelik etiket, örn. 'Kurumsal müteahhit', "
            "'Bireysel villa alıcısı', 'Yatırımcı arazi'"
        ),
    )
    evidence: list[str] = Field(default_factory=list, max_length=6)


class RiskSignals(BaseModel):
    """Risk göstergeleri — skora ceza uygular."""

    disposable_email: bool = False
    suspicious_phone_pattern: bool = Field(
        default=False,
        description=(
            "Aynı rakam tekrarı (5555...), klavye deseni (12345), "
            "test numaraları, formatsız"
        ),
    )
    data_inconsistency_count: int = Field(
        default=0, ge=0, le=10,
        description="İç tutarsızlık sayısı — ör. 'notes villa diyor ama project_type=commercial'",
    )
    spam_indicator_count: int = Field(
        default=0, ge=0, le=10,
        description="Rastgele metin, kopya-yapıştırma, hakaret, form spam",
    )
    competitor_mentioned: bool = False
    evidence: list[str] = Field(default_factory=list, max_length=5)


class SalesContext(BaseModel):
    """Satış ekibine ilk arama için hazırlıklı bağlam (her zaman İngilizce).

    Bu alanlar LLM tarafından üretilen NARRATIF içeriktir — enum değil.
    Skora doğrudan katkısı yok ama glass-box panel'de + outbound webhook
    payload'ında satış ekibine gider. CRM frontend gerekirse sales rep'in
    tercih ettiği dile çevirir.
    """

    who_they_are: str = Field(max_length=300)
    company_or_buyer_profile: str = Field(max_length=250)
    recommended_opening: str = Field(max_length=250)
    risks_to_watch: list[str] = Field(default_factory=list, max_length=4)
    key_questions_for_call: list[str] = Field(min_length=2, max_length=5)


class PreScoreJudgmentResult(BaseModel):
    """LLM'den beklenen tam yapı. Groq json_object mode + Pydantic validation.

    Ensemble pattern (Phase 2): 3 persona paralel çalışır, her biri bu yapıyı
    üretir. Ana skor `direct_score` field'ından gelir; median alınır.
    Composer (audit layer) signals'dan ayrı bir shadow score üretir ve
    divergence > 20 durumunda flag basar.

    Field sırası anti-bias için önemli: thinking önce (CoT), extraction arada,
    direct_score + confidence EN SONDA. Bu sıra 2026 LLM-as-judge best
    practice: Chain-of-thought before commitment, evidence before number.
    """

    thinking: str = Field(
        max_length=2500,
        description="Chain-of-thought scratchpad — senin iç mantığın. Sales ekibine gitmeyecek.",
    )
    identity: IdentitySignals
    intent: IntentSignals
    fit: FitSignals
    risk: RiskSignals
    sales_context: SalesContext
    direct_score: int = Field(
        ge=0, le=100,
        description=(
            "Bu lead için 0-100 puan. Sinyalleri + sales_context'i "
            "değerlendirdikten SONRA ver. CoT (thinking) ve kanıt extract "
            "(identity/intent/fit/risk) bittikten sonra bu rakamı üret. "
            "Ensemble medianı bu alandan hesaplanır."
        ),
    )
    extraction_confidence: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Bu extraction'a ne kadar güveniyorsun (0-1). Eksik/çelişkili "
            "veride düşük (0.3-0.5). Ensemble aggregation'da sales_context "
            "seçimi ve düşük-confidence flag için kullanılır."
        ),
    )
