"""
Turkish real-estate (Cyprus/KKTC investor) sector few-shot examples for CHAMP extraction.

Examples are calibrated for residential investor leads: villa/apartment/studio
shoppers evaluating Cyprus property for rental yield, flip, or holiday-let use.
sector_qualifiers use the real-estate schema (has_property_shortlist,
financing_ready, visit_intent, decision_partner_aligned, exit_strategy_clear).
"""

EXAMPLES: list[dict] = [
    {
        "conversation": (
            'MÜŞTERİ: "Girne tarafında deniz manzaralı villa bakıyorum. '
            'Esentepe ve Çatalköy\'deki iki projeyi kısa listeye aldım. '
            'Yatırım amacıyla, muhtemelen Airbnb\'de değerlendireceğim. '
            'Bütçem 600-800 bin euro, nakit hazır. '
            'Kararı ben veriyorum. Uygun fırsatta 1-2 ay içinde yerinde görmek isterim."'
        ),
        "output": {
            "challenges_score": 23,
            "authority_score": 24,
            "money_score": 24,
            "prioritization_score": 22,
            "challenges_notes": "Girne/Esentepe/Çatalköy deniz manzaralı villa, Airbnb yatırımı, kısa liste net",
            "authority_notes": "Kararı kendisinin verdiğini belirtti",
            "money_notes": "600-800 bin euro nakit hazır, finansman tamam",
            "prioritization_notes": "1-2 ay içinde Kıbrıs'a gelip yerinde görme niyeti var",
            "challenges_confidence": 0.95,
            "authority_confidence": 0.95,
            "money_confidence": 0.95,
            "prioritization_confidence": 0.85,
            "confidence": "high",
            "sector_qualifiers": {
                "has_property_shortlist": 2,
                "financing_ready": "cash",
                "visit_intent": True,
                "decision_partner_aligned": True,
                "exit_strategy_clear": True,
                "property_type": "villa",
                "location": "Esentepe/Çatalköy",
            },
        },
    },
    {
        "conversation": (
            'MÜŞTERİ: "Kıbrıs\'ta yatırım düşünüyorum ama henüz tam karar vermedim. '
            'Daha çok genel bilgi almak istiyorum, hangi bölgeler var, fiyatlar nasıl?"'
        ),
        "output": {
            "challenges_score": 8,
            "authority_score": 8,
            "money_score": 2,
            "prioritization_score": 3,
            "challenges_notes": "Genel yatırım ilgisi var ama bölge ve mülk tipi belirsiz",
            "authority_notes": "Kendi adına konuşuyor gibi ama karar gücü net değil",
            "money_notes": "Bütçe bilgisi yok",
            "prioritization_notes": "Araştırma aşamasında, zamanlama yok",
            "challenges_confidence": 0.45,
            "authority_confidence": 0.35,
            "money_confidence": 0.1,
            "prioritization_confidence": 0.15,
            "confidence": "low",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": None,
                "visit_intent": False,
                "decision_partner_aligned": None,
                "exit_strategy_clear": False,
            },
        },
    },
    {
        "conversation": (
            'MÜŞTERİ: "Ben Türkiye\'deyim, önce uzaktan bilgi alayım istiyorum. '
            'Girne\'de deniz manzaralı 2+1 ya da küçük villa bakabilirim. '
            'Bütçeyi tam netleştirmedim ama kredi de düşünebilirim. '
            'Eşimle birlikte değerlendireceğiz."'
        ),
        "output": {
            "challenges_score": 17,
            "authority_score": 10,
            "money_score": 8,
            "prioritization_score": 7,
            "challenges_notes": "Girne, deniz manzarası, 2+1 veya küçük villa tercihi var",
            "authority_notes": "Eşiyle beraber değerlendiriyor, karar yapısı net değil",
            "money_notes": "Bütçe net değil, kredi seçeneğine açık",
            "prioritization_notes": "Uzaktan bilgi aşamasında, visit niyeti yok",
            "challenges_confidence": 0.8,
            "authority_confidence": 0.4,
            "money_confidence": 0.35,
            "prioritization_confidence": 0.3,
            "confidence": "medium",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": "pending",
                "visit_intent": False,
                "decision_partner_aligned": False,
                "exit_strategy_clear": False,
                "property_type": "apartment",
                "location": "Girne",
            },
        },
    },
    {
        "conversation": (
            'MÜŞTERİ: "Airbnb için deniz tarafında bir villa arıyorum. '
            'Peşinat olarak 50 bin sterlinim var, gerekirse kalan kısmı krediyle tamamlarım. '
            'Eşimle beraber değerlendireceğiz ama son kararı büyük ihtimalle ben vereceğim. '
            'Yaz aylarında Kıbrıs\'a geliyoruz zaten, o zaman gezeriz."'
        ),
        "output": {
            "challenges_score": 21,
            "authority_score": 17,
            "money_score": 16,
            "prioritization_score": 14,
            "challenges_notes": "Airbnb için deniz tarafı villa, çıkış stratejisi net",
            "authority_notes": "Eşiyle birlikte ama son karar kendisi",
            "money_notes": "50 bin sterlin peşinat + krediyle tamamlama",
            "prioritization_notes": "Yaz döneminde yerinde görme niyeti var ama tarih net değil",
            "challenges_confidence": 0.9,
            "authority_confidence": 0.7,
            "money_confidence": 0.75,
            "prioritization_confidence": 0.5,
            "confidence": "medium",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": "pending",
                "visit_intent": True,
                "decision_partner_aligned": True,
                "exit_strategy_clear": True,
                "property_type": "villa",
            },
        },
    },
    {
        "conversation": (
            'MÜŞTERİ: "KKTC tarafında ilk yatırımım olacak. '
            '1+1 ya da 2+1, denize yakın bir tarafta olsun istiyorum. '
            'Bütçem 220-280 bin sterlin civarı. 2-3 yıllık taksit ve peşinat yapısı önemli. '
            'Doğru projeyi bulursam yazdan önce ilerlemek istiyorum ve yerinde görmek isterim."'
        ),
        "output": {
            "challenges_score": 19,
            "authority_score": 12,
            "money_score": 18,
            "prioritization_score": 19,
            "challenges_notes": "İlk KKTC yatırımı, denize yakın 1+1 veya 2+1 arıyor",
            "authority_notes": "Kendi adına konuşuyor gibi, karar yapısı tam net değil",
            "money_notes": "220-280 bin sterlin bütçe, taksit ve peşinat aktif ilgisinde",
            "prioritization_notes": "Yazdan önce ilerlemek istiyor, yerinde görme niyeti var",
            "challenges_confidence": 0.85,
            "authority_confidence": 0.45,
            "money_confidence": 0.85,
            "prioritization_confidence": 0.85,
            "confidence": "high",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": "pending",
                "visit_intent": True,
                "decision_partner_aligned": False,
                "exit_strategy_clear": False,
                "property_type": "apartment",
            },
        },
    },
]


def format_few_shot_examples() -> str:
    """Format examples as a string block for injection into extraction prompt."""
    import json

    parts: list[str] = ["## Örnekler\n"]
    for i, ex in enumerate(EXAMPLES, 1):
        output_str = json.dumps(ex["output"], ensure_ascii=False, indent=2)
        parts.append(f"### Örnek {i}\n{ex['conversation']}\nSonuç:\n{output_str}\n")
    return "\n".join(parts)
