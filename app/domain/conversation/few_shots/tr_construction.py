"""
Turkish construction / real estate sector few-shot examples for CHAMP extraction.

These examples help small local models produce more consistent
qualification signals for project intent, authority, budget, and timing.
"""

EXAMPLES: list[dict] = [
    {
        "conversation": (
            'MÜŞTERİ: "Girne tarafında deniz manzaralı villa bakıyorum. '
            'Daha çok yatırım için düşünüyorum. Bütçem 600-800 bin euro civarı. '
            'Kararı ben veriyorum. Uygun bir fırsat çıkarsa 1-2 ay içinde ilerleyebilirim."'
        ),
        "output": {
            "challenges_score": 22,
            "authority_score": 24,
            "money_score": 23,
            "prioritization_score": 20,
            "challenges_notes": "Girne, deniz manzaralı villa, yatırım amacı net",
            "authority_notes": "Kararı kendisinin verdiğini belirtti",
            "money_notes": "600-800 bin euro bütçe belirtildi",
            "prioritization_notes": "Uygun fırsatta 1-2 ay içinde ilerleyebilir",
            "challenges_confidence": 0.9,
            "authority_confidence": 0.95,
            "money_confidence": 0.9,
            "prioritization_confidence": 0.8,
            "confidence": "high",
        },
    },
    {
        "conversation": (
            'MÜŞTERİ: "Kıbrıs’ta yatırım düşünüyorum ama henüz tam karar vermedim. '
            'Daha çok genel bilgi almak istiyorum."'
        ),
        "output": {
            "challenges_score": 8,
            "authority_score": 8,
            "money_score": 2,
            "prioritization_score": 3,
            "challenges_notes": "Genel yatırım ilgisi var ama proje tipi belirsiz",
            "authority_notes": "Kendi adına konuşuyor gibi ama karar gücü net değil",
            "money_notes": "Bütçe bilgisi yok",
            "prioritization_notes": "Zamanlama belirtmedi",
            "challenges_confidence": 0.45,
            "authority_confidence": 0.35,
            "money_confidence": 0.1,
            "prioritization_confidence": 0.15,
            "confidence": "low",
        },
    },
    {
        "conversation": (
            'MÜŞTERİ: "Ben Türkiye’deyim, önce uzaktan bilgi alayım istiyorum. '
            'Girne’de deniz manzaralı 2+1 ya da küçük villa bakabilirim. '
            'Bütçeyi netleştirmedim ama kredi de düşünebilirim."'
        ),
        "output": {
            "challenges_score": 17,
            "authority_score": 10,
            "money_score": 8,
            "prioritization_score": 7,
            "challenges_notes": "Girne, deniz manzarası, 2+1 veya küçük villa tercihi var",
            "authority_notes": "Kendisi için bakıyor gibi görünüyor ama karar durumu net değil",
            "money_notes": "Bütçe net değil, kredi seçeneğine açık",
            "prioritization_notes": "Uzaktan bilgi aşamasında, aciliyet düşük/orta",
            "challenges_confidence": 0.8,
            "authority_confidence": 0.4,
            "money_confidence": 0.35,
            "prioritization_confidence": 0.3,
            "confidence": "medium",
        },
    },
    {
        "conversation": (
            'MÜŞTERİ: "Airbnb için deniz tarafında bir villa arıyorum. '
            'Peşinat olarak 50 bin sterlinim var. Gerekirse kalan kısmı krediyle tamamlarım. '
            'Eşimle beraber değerlendireceğiz ama son kararı büyük ihtimalle ben vereceğim."'
        ),
        "output": {
            "challenges_score": 20,
            "authority_score": 16,
            "money_score": 16,
            "prioritization_score": 10,
            "challenges_notes": "Airbnb amaçlı deniz tarafında villa arıyor, kullanım amacı net",
            "authority_notes": "Eşiyle beraber değerlendiriyor ama karar ağırlığı kendisinde",
            "money_notes": "50 bin sterlin peşinat var, krediye açık",
            "prioritization_notes": "Zamanlama net değil",
            "challenges_confidence": 0.9,
            "authority_confidence": 0.7,
            "money_confidence": 0.75,
            "prioritization_confidence": 0.25,
            "confidence": "medium",
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

    