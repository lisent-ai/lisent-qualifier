"""
Turkish construction sector few-shot examples for CHAMP extraction.

These examples help small local models (4B-9B params) produce
accurate, calibrated scores.
"""

EXAMPLES: list[dict] = [
    {
        "conversation": (
            'MÜŞTERİ: "5 milyon bütçemiz var, Antalya\'da otel inşaatı düşünüyoruz. '
            'Kararı ben veriyorum, 3 ay içinde başlamak istiyoruz."'
        ),
        "output": {
            "challenges_score": 20,
            "authority_score": 23,
            "money_score": 22,
            "prioritization_score": 20,
            "challenges_notes": "Otel inşaatı, Antalya, net proje tipi",
            "authority_notes": "Tek karar verici olarak belirtti",
            "money_notes": "5M TL bütçe belirtildi",
            "prioritization_notes": "3 ay içinde başlamak istiyor",
            "challenges_confidence": 0.8,
            "authority_confidence": 0.9,
            "money_confidence": 0.85,
            "prioritization_confidence": 0.85,
            "confidence": "high",
        },
    },
    {
        "conversation": 'MÜŞTERİ: "İnşaat maliyetleri ne kadar?"',
        "output": {
            "challenges_score": 5,
            "authority_score": 0,
            "money_score": 3,
            "prioritization_score": 0,
            "challenges_notes": "Genel inşaat ilgisi, spesifik proje yok",
            "authority_notes": "Bilgi yok",
            "money_notes": "Fiyat soruyor ama bütçe belirtmedi",
            "prioritization_notes": "Zaman bilgisi yok",
            "challenges_confidence": 0.2,
            "authority_confidence": 0.0,
            "money_confidence": 0.1,
            "prioritization_confidence": 0.0,
            "confidence": "low",
        },
    },
    {
        "conversation": (
            'MÜŞTERİ: "Arsam var, imarlı. Villa yaptırmak istiyorum ama '
            'bütçeyi henüz netleştirmedim."'
        ),
        "output": {
            "challenges_score": 18,
            "authority_score": 10,
            "money_score": 5,
            "prioritization_score": 8,
            "challenges_notes": "Villa projesi, arsası var, imar onaylı",
            "authority_notes": "Kendisi için bakıyor ama karar durumu belirsiz",
            "money_notes": "Bütçe henüz netleşmemiş",
            "prioritization_notes": "Aciliyet belirtmedi ama arsa hazır",
            "challenges_confidence": 0.85,
            "authority_confidence": 0.4,
            "money_confidence": 0.2,
            "prioritization_confidence": 0.3,
            "confidence": "medium",
        },
    },
    {
        "conversation": (
            'MÜŞTERİ: "3 katlı ticari bina, 1500m², İstanbul Ataşehir\'de. '
            'Ortağımla beraber karar vereceğiz. Bütçemiz 8-10 milyon arası, '
            'nakit ödeyeceğiz. Bu yıl içinde bitirmemiz lazım."'
        ),
        "output": {
            "challenges_score": 24,
            "authority_score": 15,
            "money_score": 24,
            "prioritization_score": 18,
            "challenges_notes": "3 katlı ticari, 1500m², Ataşehir, çok net proje",
            "authority_notes": "Ortakla ortak karar",
            "money_notes": "8-10M TL, nakit ödeme",
            "prioritization_notes": "Yıl içinde bitirilmesi gerekiyor",
            "challenges_confidence": 0.95,
            "authority_confidence": 0.7,
            "money_confidence": 0.9,
            "prioritization_confidence": 0.8,
            "confidence": "high",
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
