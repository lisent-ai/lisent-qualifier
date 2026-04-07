"""
Turkish construction / real estate sector few-shot anchors for qualification judge.

5 calibration examples spanning the full score range (15, 35, 55, 75, 92).
These anchor the LLM's scoring to prevent drift and ensure consistency.
Domain: Cyprus (KKTC) real estate — villas, apartments, investment properties.
"""
import json

JUDGE_EXAMPLES: list[dict] = [
    # ── Score ~92: Highly qualified lead ──────────────────────────────────
    {
        "conversation": (
            'MUSTERI: "Girne\'de deniz manzarali villa ariyorum, yatirim ve kendi kullanim icin. '
            'Butcem 600-800 bin euro, nakidim hazir. Karar verici benim, esim de destekliyor '
            'ama son karar bende. 1-2 ay icinde ilerlemek istiyorum. Daha once Kibris\'a '
            'geldim, bolgeyi biliyorum. Kira getirisi potansiyelini de dusunuyorum."'
        ),
        "output": {
            "thinking": (
                "1. Proje cok net: Girne, deniz manzarali villa, yatirim + kendi kullanim. "
                "2. Karar verici kendisi, es destekliyor ama son karar onda — otorite cok guclu. "
                "3. 600-800K EUR nakit — ciddi ve hazir butce. "
                "4. 1-2 ay icinde ilerleme — cok acil. "
                "5. Bolgeyi biliyor, daha once gelmis — ciddiyet yuksek. "
                "6. Kira getirisi dusunuyor — yatirim bilinciyle hareket ediyor. "
                "7. Kirmizi bayrak yok. Tam ICP uyumu."
            ),
            "challenges_score": 24,
            "challenges_reasoning": "Girne, deniz manzarali villa, yatirim + kendi kullanim — cok net proje tanimi",
            "challenges_confidence": 0.95,
            "authority_score": 24,
            "authority_reasoning": "'Karar verici benim, esim destekliyor ama son karar bende' — guclu otorite",
            "authority_confidence": 0.95,
            "money_score": 24,
            "money_reasoning": "600-800K EUR nakit butce belirtildi — hazir ve uyumlu",
            "money_confidence": 0.95,
            "prioritization_score": 22,
            "prioritization_reasoning": "'1-2 ay icinde ilerlemek istiyorum' — cok acil",
            "prioritization_confidence": 0.9,
            "holistic_score": 92,
            "holistic_reasoning": "ICP ile tam uyum. Butun boyutlar guclu. Butce hazir, karar verici net, zaman acil, bolgeyi biliyor. Kira getirisi bilinci ciddiyet gosteriyor.",
            "icp_fit_assessment": "Tam uyum — yatirim amacli villa, 600K+ EUR nakit, tek karar verici, 2 ay icinde, bolgeyi bilen alici",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": "nakit",
                "competing_bids": None,
                "project_sqm": None,
            },
            "missing_info": ["Tercih ettigi spesifik proje veya bolge detayi"],
            "recommended_next_question": "Girne'nin hangi tarafi daha cok ilginizi cekiyor — Alsancak, Lapta tarafi mi yoksa merkez mi?",
            "confidence": "high",
        },
    },
    # ── Score ~75: Good lead with some gaps ──────────────────────────────
    {
        "conversation": (
            'MUSTERI: "Esimle beraber bakiyoruz. Girne\'de 2+1 veya 3+1 daire dusunuyoruz, '
            'deniz manzarasi olsa guzel olur. Hem tatil evi hem Airbnb geliri amaciyla. '
            'Butcemiz 250-350 bin euro arasi. Yazdan once karar vermek isteriz ama henuz '
            'Kibris\'a gelmedik."'
        ),
        "output": {
            "thinking": (
                "1. Mulk tipi net: 2+1 veya 3+1 daire, deniz manzarasi tercihi. "
                "2. Esle ortak karar — sole authority degil ama ikisi de karar surecinde. "
                "3. 250-350K EUR aralik belirtildi — makul ama kesin degil. "
                "4. 'Yazdan once karar vermek isteriz' — orta vadeli plan, 3-4 ay. "
                "5. Henuz Kibris'a gelmemis — fiziksel gorusme gerekecek, ek adim. "
                "6. Airbnb geliri amaci — yatirim bilinci var. "
                "7. Kirmizi bayrak yok, ciddi bir cift izlenimi."
            ),
            "challenges_score": 18,
            "challenges_reasoning": "2+1/3+1 daire, deniz manzarasi, tatil + Airbnb — net tercih ama spesifik proje secimi yok",
            "challenges_confidence": 0.8,
            "authority_score": 14,
            "authority_reasoning": "'Esimle beraber bakiyoruz' — ortak karar, sole authority degil",
            "authority_confidence": 0.7,
            "money_score": 18,
            "money_reasoning": "'250-350 bin euro arasi' — aralik belirtildi, ciddi butce",
            "money_confidence": 0.8,
            "prioritization_score": 15,
            "prioritization_reasoning": "'Yazdan once karar vermek isteriz' — 3-4 ay, orta aciliyet",
            "prioritization_confidence": 0.65,
            "holistic_score": 75,
            "holistic_reasoning": "Ciddi cift, net mulk tercihi ve butce araligi var. Airbnb gelir amaci yatirim bilinci gosteriyor. Eksikler: henuz Kibris'a gelmemis, esle ortak karar. Takip edilmeli, ziyaret planlanmali.",
            "icp_fit_assessment": "Iyi uyum — butce uyumlu, mulk tercihi net, zaman var ama henuz fiziksel adim atilmamis",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None,
            },
            "missing_info": ["Odeme sekli (nakit/kredi)", "Kibris'a gelis plani", "Spesifik proje tercihi"],
            "recommended_next_question": "Kibris'a gelmeyi ne zaman dusunuyorsunuz? Oncesinde uzaktan bilgilendirme yapabiliriz.",
            "confidence": "medium",
        },
    },
    # ── Score ~55: Borderline lead ───────────────────────────────────────
    {
        "conversation": (
            'MUSTERI: "Kibris\'ta gayrimenkul yatirimi dusunuyorum ama henuz net bir planım yok. '
            'Ne tur mulkler var, fiyatlar nasil? Belki gelecek sene bir seyler yapariz."'
        ),
        "output": {
            "thinking": (
                "1. Genel yatirim ilgisi var ama mulk tipi belirsiz. "
                "2. Kendi adina konusuyor — muhtemelen karar verici ama net degil. "
                "3. Butce paylasmamis, 'fiyatlar nasil' genel bilgi talebi. "
                "4. 'Belki gelecek sene' — uzun vadeli, belirsiz zamanlama. "
                "5. Henuz cok erken asama ama potansiyel var. "
                "6. Kirmizi bayrak yok ama ciddiyet dusuk."
            ),
            "challenges_score": 6,
            "challenges_reasoning": "'Gayrimenkul yatirimi dusunuyorum' — genel ilgi, somut mulk tercihi yok",
            "challenges_confidence": 0.35,
            "authority_score": 10,
            "authority_reasoning": "Kendi yatirimi olarak bahsediyor — muhtemelen karar verici",
            "authority_confidence": 0.45,
            "money_score": 3,
            "money_reasoning": "Butce paylasmadi, sadece 'fiyatlar nasil' soruyor",
            "money_confidence": 0.2,
            "prioritization_score": 5,
            "prioritization_reasoning": "'Belki gelecek sene' — belirsiz, uzun vadeli",
            "prioritization_confidence": 0.3,
            "holistic_score": 55,
            "holistic_reasoning": "Erken asamada potansiyel musteri. Yatirim ilgisi var ama mulk tipi, butce ve zaman belirsiz. Bilgilendirme ile ilerlenebilir ama acil handoff gereksiz.",
            "icp_fit_assessment": "Dusuk uyum — yatirim ilgisi var ama somut plan, butce ve zaman eksik",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None,
            },
            "missing_info": ["Mulk tipi tercihi", "Butce araligi", "Zamanlama", "Yatirim amaci detayi"],
            "recommended_next_question": "Daha cok ne tur bir mulk ilginizi cekiyor — villa mi, daire mi?",
            "confidence": "low",
        },
    },
    # ── Score ~35: Low quality lead ──────────────────────────────────────
    {
        "conversation": (
            'MUSTERI: "Kuzey Kibris\'ta fiyatlar nasil? Ne kadara mulk alinabiliyor?"'
        ),
        "output": {
            "thinking": (
                "1. Sadece fiyat soruyor — somut mulk tercihi yok. "
                "2. Karar verici durumu bilinmiyor. "
                "3. Butce paylasmadan fiyat soruyor — price fishing riski. "
                "4. Zaman cizelgesi yok. "
                "5. Henuz tek mesaj, kesin degerlendirme icin erken. "
                "6. Potansiyel var ama ciddiyet sinyali dusuk."
            ),
            "challenges_score": 3,
            "challenges_reasoning": "'Fiyatlar nasil, ne kadara alinabiliyor' — genel bilgi talebi, mulk tercihi yok",
            "challenges_confidence": 0.25,
            "authority_score": 0,
            "authority_reasoning": "Hicbir bilgi yok",
            "authority_confidence": 0.1,
            "money_score": 2,
            "money_reasoning": "Fiyat soruyor ama kendi butcesi hakkinda bilgi yok",
            "money_confidence": 0.15,
            "prioritization_score": 0,
            "prioritization_reasoning": "Zaman bilgisi yok",
            "prioritization_confidence": 0.1,
            "holistic_score": 35,
            "holistic_reasoning": "Genel bilgi talebi. Mulk tercihi, butce, yetki ve zaman eksik. Price fishing riski var ama tek mesajla kesin degil. Daha fazla bilgi toplama gerekli.",
            "icp_fit_assessment": "Cok dusuk uyum — hicbir ICP kriteri karsilanmiyor",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "Henuz tek mesaj, kesin negatif sinyal yok",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None,
            },
            "missing_info": ["Mulk tipi tercihi", "Konum tercihi", "Butce", "Karar verici durumu", "Zamanlama"],
            "recommended_next_question": "Ne tur bir mulk dusunuyorsunuz — daire mi, villa mi?",
            "confidence": "low",
        },
    },
    # ── Score ~15: Disqualified / competitor ─────────────────────────────
    {
        "conversation": (
            'MUSTERI: "Biz de emlak sektorundeyiz, piyasa karsilastirmasi yapiyoruz. '
            'Fiyat listenizi paylasir misiniz?"'
        ),
        "output": {
            "thinking": (
                "1. Acikca emlak sektorunde oldugunu soyledi. "
                "2. 'Piyasa karsilastirmasi' — musteri degil, rakip istihbarati. "
                "3. Bu lead diskalifiye edilmeli. "
                "4. Hicbir CHAMP boyutu gecerli degil."
            ),
            "challenges_score": 0,
            "challenges_reasoning": "Rakip firma — gercek bir mulk alim niyeti yok",
            "challenges_confidence": 0.9,
            "authority_score": 0,
            "authority_reasoning": "Musteri degil, sektor calisani",
            "authority_confidence": 0.9,
            "money_score": 0,
            "money_reasoning": "Alis butcesi yok — piyasa arastirmasi",
            "money_confidence": 0.9,
            "prioritization_score": 0,
            "prioritization_reasoning": "Satin alma niyeti yok",
            "prioritization_confidence": 0.9,
            "holistic_score": 5,
            "holistic_reasoning": "Rakip / sektor calisani — diskalifiye. Satis ekibinin zamani harcanmamali.",
            "icp_fit_assessment": "Uyumsuz — potansiyel musteri degil, rakip/sektor arastirmasi",
            "negative_signals": ["competitor"],
            "negative_penalty": -100,
            "negative_reasoning": "'Biz de emlak sektorundeyiz, piyasa karsilastirmasi' — acik rakip beyani",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None,
            },
            "missing_info": [],
            "recommended_next_question": "",
            "confidence": "high",
        },
    },
]


def format_judge_few_shot_examples() -> str:
    """Format examples for prompt injection."""
    parts = ["## Kalibrasyon Ornekleri\n"]
    for i, ex in enumerate(JUDGE_EXAMPLES, 1):
        parts.append(f"### Ornek {i}")
        parts.append(f"Konusma: {ex['conversation']}")
        parts.append(f"Beklenen Cikti:\n```json\n{json.dumps(ex['output'], ensure_ascii=False, indent=2)}\n```\n")
    return "\n".join(parts)
