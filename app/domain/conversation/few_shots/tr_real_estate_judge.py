"""
Turkish Cyprus real-estate investor few-shot anchors for qualification judge.

4 calibration examples covering all 3 CTA bands:
  1. High-score + visit intent    → cta_recommendation: cyprus_visit
  2. Medium-score + engaged       → cta_recommendation: calendly
  3. Low-score + early research   → cta_recommendation: nurture
  4. Instant disqualify (realtor) → cta_recommendation: nurture (no handoff)
"""
import json

JUDGE_EXAMPLES: list[dict] = [
    {
        "conversation": (
            'MUSTERI: "Esentepe tarafinda denize yakin bir villa bakiyorum. '
            'Bunu yatirim icin dusunuyorum; Airbnb\'ye acigim. '
            'Butcem 700-900 bin euro, nakit hazir. Karar verici benim. '
            'Bu yaz Kibris\'a gelip yerinde gormek istiyorum. Misafir olabilirim birkac gun."'
        ),
        "output": {
            "thinking": (
                "1. Musteri gercek alici ve hedefi net. "
                "2. Segment yatirimci; Airbnb cikisi net. "
                "3. 700-900K EUR nakit hazir - guclu butce. "
                "4. Karar verici net. "
                "5. Yaz doneminde Kibris'a gelip yerinde gorme niyeti acikca belirtildi. "
                "6. Holistic yuksek; misafir olma ifadesi tam cyprus_visit CTA'sina uygun."
            ),
            "challenges_score": 23,
            "challenges_reasoning": "Esentepe villa, Airbnb cikisi ve yatirim modeli net",
            "challenges_confidence": 0.9,
            "authority_score": 24,
            "authority_reasoning": "Karar verici net",
            "authority_confidence": 0.95,
            "money_score": 24,
            "money_reasoning": "700-900K EUR nakit hazir",
            "money_confidence": 0.95,
            "prioritization_score": 22,
            "prioritization_reasoning": "Yaz donemi visit niyeti net",
            "prioritization_confidence": 0.9,
            "holistic_score": 92,
            "holistic_reasoning": "Lead handoff'a tamamen hazir; Kibris'a gelme niyeti acik.",
            "icp_fit_assessment": "Tam uyum - ciddi yatirimci, net butce, net visit niyeti",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": "cash",
                "visit_intent": True,
                "decision_partner_aligned": True,
                "exit_strategy_clear": True,
                "property_type": "villa",
                "location": "Esentepe",
            },
            "missing_info": [],
            "recommended_next_question": "",
            "handoff_ready": True,
            "handoff_reason": "Butce, visit niyeti ve karar verici netligi ile cyprus_visit'e uygun",
            "cta_recommendation": "cyprus_visit",
            "confidence": "high",
            "extracted_budget_range": "500k_1m",
            "extracted_budget_amount": 900000,
            "extracted_project_type": "residential_investor",
            "extracted_timeline_urgency": "short",
            "extracted_decision_authority": "sole",
            "extracted_city": "Esentepe",
            "extracted_project_details": "villa yatirimi, Airbnb odakli, yerinde gorme niyeti",
        },
    },
    {
        "conversation": (
            'MUSTERI: "Yatirim amacli bir sey ariyorum Kibris\'ta, muhtemelen stüdyo. '
            'Butcem 150-200 bin sterlin civari, muhtemelen kredi + pesinat. '
            'Yilin farkli zamanlarinda Airbnb\'ye verebilirim. '
            'Esimle konustuk, karari birlikte verecegiz. '
            'Surec nasil isliyor detayli anlamak isterim."'
        ),
        "output": {
            "thinking": (
                "1. Yatirim niyeti ciddi, stüdyo + Airbnb cikisi belirli. "
                "2. Butce verildi ama finansman kesin degil (pending). "
                "3. Karar ortak - netlesmesi gereken bir nokta. "
                "4. Visit niyeti acikca yok ama engaged ve cevap veriyor. "
                "5. Detay ve online gorusme icin olgun, ama yerinde gorme icin henuz erken. "
                "6. Calendly uygun - Redif'le 30dk surec konusulsun."
            ),
            "challenges_score": 17,
            "challenges_reasoning": "Stüdyo + Airbnb, cikis stratejisi belirli",
            "challenges_confidence": 0.8,
            "authority_score": 14,
            "authority_reasoning": "Ortak karar, netlik eksik",
            "authority_confidence": 0.7,
            "money_score": 15,
            "money_reasoning": "Butce araligi verildi, finansman pending",
            "money_confidence": 0.7,
            "prioritization_score": 12,
            "prioritization_reasoning": "Sureci anlamak istiyor, yakin zamanlama sinyali yok",
            "prioritization_confidence": 0.55,
            "holistic_score": 62,
            "holistic_reasoning": "Engaged ve ciddi bir lead; sureci detaylandirmak ve pesinat/finansman netlestirmek icin online gorusme ideal.",
            "icp_fit_assessment": "Orta-ust uyum - ciddi yatirimci ama bir kac detay eksik",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": "pending",
                "visit_intent": False,
                "decision_partner_aligned": False,
                "exit_strategy_clear": True,
                "property_type": "studio",
            },
            "missing_info": ["Visit niyeti", "Finansman detayi", "Ortak karar netligi"],
            "recommended_next_question": "",
            "handoff_ready": True,
            "handoff_reason": "Detay ve online gorusme icin olgun; calendly uygun",
            "cta_recommendation": "calendly",
            "confidence": "medium",
            "extracted_budget_range": "under_500k",
            "extracted_budget_amount": 200000,
            "extracted_project_type": "residential_investor",
            "extracted_timeline_urgency": "medium",
            "extracted_decision_authority": "joint",
            "extracted_city": "",
            "extracted_project_details": "stüdyo yatirimi, Airbnb cikisi",
        },
    },
    {
        "conversation": (
            'MUSTERI: "Kibris\'ta yatirim dusunuyorum ama daha cok arastirma asamasindayim. '
            'Belki studio belki 1+1, henuz net degil. '
            'Fiyat bandiniz nasil, bir liste var mi?"'
        ),
        "output": {
            "thinking": (
                "1. Erken kesif asamasinda. "
                "2. Butce yok, karar yapisi yok, timeline yok. "
                "3. Fiyat odakli soru var - ama tek sefer, price_fishing sinyali cok zayif. "
                "4. Engagement dusuk, persuadable degil. "
                "5. Nurture uygun; baskisiz kapanis."
            ),
            "challenges_score": 7,
            "challenges_reasoning": "Studio veya 1+1 genel ilgi, tip/strateji yok",
            "challenges_confidence": 0.4,
            "authority_score": 5,
            "authority_reasoning": "Karar yapisi yok",
            "authority_confidence": 0.3,
            "money_score": 3,
            "money_reasoning": "Butce paylasilmadi",
            "money_confidence": 0.2,
            "prioritization_score": 4,
            "prioritization_reasoning": "Zamanlama belirsiz, arastirma modunda",
            "prioritization_confidence": 0.25,
            "holistic_score": 38,
            "holistic_reasoning": "Potansiyel var ama erken asama; bastirmak yerine nurture mode'da birakilmali.",
            "icp_fit_assessment": "Alt-orta uyum - ilgi var, ciddiyet yok",
            "negative_signals": ["just_looking"],
            "negative_penalty": -5,
            "negative_reasoning": "Arastirma asamasinda, fiyat odakli ilk mesaj",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": None,
                "visit_intent": False,
                "decision_partner_aligned": None,
                "exit_strategy_clear": False,
            },
            "missing_info": ["Butce", "Yatirim modeli", "Karar sureci"],
            "recommended_next_question": "",
            "handoff_ready": False,
            "handoff_reason": "Erken asama; baskisiz nurture",
            "cta_recommendation": "nurture",
            "confidence": "low",
            "extracted_budget_range": "",
            "extracted_budget_amount": None,
            "extracted_project_type": "residential_investor",
            "extracted_timeline_urgency": "long",
            "extracted_decision_authority": "",
            "extracted_city": "",
            "extracted_project_details": "genel arastirma",
        },
    },
    {
        "conversation": (
            'MUSTERI: "Biz de emlak sektorundeyiz, piyasayi karsilastiriyoruz. '
            'Aktif fiyat listenizi paylasir misiniz?"'
        ),
        "output": {
            "thinking": (
                "1. Musteri degil, emlak sektorunde calisan. "
                "2. Amac piyasa karsilastirmasi. "
                "3. Diskalifiye edilmeli. Handoff yok, CTA yok."
            ),
            "challenges_score": 0,
            "challenges_reasoning": "Alim ihtiyaci yok",
            "challenges_confidence": 0.95,
            "authority_score": 0,
            "authority_reasoning": "Sektor profesyoneli",
            "authority_confidence": 0.95,
            "money_score": 0,
            "money_reasoning": "Alis butcesi yok",
            "money_confidence": 0.95,
            "prioritization_score": 0,
            "prioritization_reasoning": "Zamanlama yok",
            "prioritization_confidence": 0.95,
            "holistic_score": 5,
            "holistic_reasoning": "Rakip emlakci; diskalifiye.",
            "icp_fit_assessment": "Uyumsuz",
            "negative_signals": ["competitor"],
            "negative_penalty": -100,
            "negative_reasoning": "Emlak sektorunde oldugunu acikca belirtti",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": None,
                "visit_intent": False,
                "decision_partner_aligned": None,
                "exit_strategy_clear": False,
            },
            "missing_info": [],
            "recommended_next_question": "",
            "handoff_ready": False,
            "handoff_reason": "Rakip emlakci; handoff gereksiz",
            "cta_recommendation": "nurture",
            "confidence": "high",
            "extracted_budget_range": "",
            "extracted_budget_amount": None,
            "extracted_project_type": "",
            "extracted_timeline_urgency": "",
            "extracted_decision_authority": "",
            "extracted_city": "",
            "extracted_project_details": "",
        },
    },
]


def format_judge_few_shot_examples() -> str:
    """Format examples for prompt injection."""
    parts = ["## Kalibrasyon Ornekleri\n"]
    for i, ex in enumerate(JUDGE_EXAMPLES, 1):
        parts.append(f"### Ornek {i}")
        parts.append(f"Konusma: {ex['conversation']}")
        parts.append(
            f"Beklenen Cikti:\n```json\n{json.dumps(ex['output'], ensure_ascii=False, indent=2)}\n```\n"
        )
    return "\n".join(parts)
