"""
Turkish construction sector few-shot anchors for qualification judge.

5 calibration examples spanning the full score range (15, 35, 55, 75, 92).
These anchor the LLM's scoring to prevent drift and ensure consistency.
"""
import json

JUDGE_EXAMPLES: list[dict] = [
    # ── Score ~92: Highly qualified lead ──────────────────────────────────
    {
        "conversation": (
            'MUSTERI: "Antalya Konyaalti\'nda 2 donum arsam var, imari ticari. '
            '15 odali butik otel yapmak istiyoruz. Mimari ile onproje hazir. '
            'Butcemiz 12 milyon TL, nakitimiz hazir. Ben karar vericiyim, '
            'sahsima ait bir yatirim. 2 ay icinde insaata baslamak istiyoruz. '
            'Baska firmalardan da teklif aldik ama sizin referanslariniz etkiledi."'
        ),
        "output": {
            "thinking": (
                "1. Proje cok net: 15 odali butik otel, Konyaalti. Somut gereksinimler var. "
                "2. Karar verici kendisi, sahsi yatirim — otorite cok guclu. "
                "3. 12M TL nakit butce, insaat kapsamiyla uyumlu. "
                "4. 2 ay icinde baslama — cok acil. "
                "5. ICP: arsasi var, imari ticari, mimari ile calisiyor, nakit — tam uyum. "
                "6. Kirmizi bayrak yok. Competing bids pozitif sinyal (ciddi alici). "
                "7. Sektor sinyalleri: arsa var, imar hazir, mimar var, nakit butce."
            ),
            "challenges_score": 24,
            "challenges_reasoning": "15 odali butik otel, Konyaalti, 2 donum arsa — cok somut proje tanimi",
            "challenges_confidence": 0.95,
            "authority_score": 25,
            "authority_reasoning": "'Ben karar vericiyim, sahsima ait yatirim' — tek karar verici",
            "authority_confidence": 1.0,
            "money_score": 24,
            "money_reasoning": "12M TL nakit butce belirtildi, kapsamla uyumlu",
            "money_confidence": 0.95,
            "prioritization_score": 23,
            "prioritization_reasoning": "'2 ay icinde insaata baslamak istiyoruz' — cok acil",
            "prioritization_confidence": 0.9,
            "holistic_score": 92,
            "holistic_reasoning": "ICP ile neredeyse tam uyum. Butun boyutlar guclu. Arsasi, imari, mimari, butcesi, zamani hazir. Competing bids ciddiyet gostergesi.",
            "icp_fit_assessment": "Tam uyum — 500m2+ ticari proje, 12M butce, karar verici, 2 ay icinde, arsasi ve imari hazir",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": True,
                "permit_status": "imarli",
                "has_architect": True,
                "budget_source": "nakit",
                "competing_bids": True,
                "project_sqm": None,
            },
            "missing_info": ["Tahmini metrekare bilgisi"],
            "recommended_next_question": "Projenin tahmini metrekaresi ne kadar olacak?",
            "handoff_ready": True,
            "handoff_reason": "Tum CHAMP boyutlari guclu, ICP tam uyum, proje acil",
            "confidence": "high",
        },
    },
    # ── Score ~75: Good lead with some gaps ──────────────────────────────
    {
        "conversation": (
            'MUSTERI: "Istanbul\'da villa yaptirmak istiyoruz, esimle beraber karar verecegiz. '
            'Butcemiz 5-7 milyon arasi. Arsaya bakiyoruz heniiz bulamadik. '
            '6 ay icinde baslamak isteriz."'
        ),
        "output": {
            "thinking": (
                "1. Villa projesi, Istanbul — net proje tipi ama detaylar eksik (kac m2, kac kat?). "
                "2. Esle ortak karar — sole authority degil ama karar surecinde. "
                "3. 5-7M TL aralik belirtildi, makul ama kesin degil. "
                "4. 6 ay icinde baslama — orta aciliyet. "
                "5. ICP: arsa henuz yok — buyuk eksik. Butce uyumlu. "
                "6. Kirmizi bayrak yok, ciddi bir alici izlenimi."
            ),
            "challenges_score": 15,
            "challenges_reasoning": "Villa, Istanbul — proje tipi belli ama detaylar (m2, kat, ozellikler) eksik",
            "challenges_confidence": 0.7,
            "authority_score": 15,
            "authority_reasoning": "'Esimle beraber karar verecegiz' — ortak karar verici",
            "authority_confidence": 0.7,
            "money_score": 20,
            "money_reasoning": "'5-7 milyon arasi' — net aralik belirtildi",
            "money_confidence": 0.8,
            "prioritization_score": 15,
            "prioritization_reasoning": "'6 ay icinde baslamak isteriz' — orta vadeli plan",
            "prioritization_confidence": 0.7,
            "holistic_score": 75,
            "holistic_reasoning": "Ciddi alici izlenimi. Butce uyumlu, zaman cizelgesi var. Ana eksik: arsa henuz yok, proje detaylari belirsiz. Esle ortak karar riski dusuk.",
            "icp_fit_assessment": "Kismi uyum — butce ve zaman uyumlu ama arsa yok, proje detaylari eksik",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": False,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None,
            },
            "missing_info": ["Proje metrekaresi", "Kat sayisi", "Arsa durumu detayi", "Mimar ile calisiyorlar mi"],
            "recommended_next_question": "Villanin yaklasik kac metrekare olmasini dusunuyorsunuz?",
            "handoff_ready": False,
            "handoff_reason": "Arsa durumu ve proje detaylari henuz eksik, sohbet ilerliyor",
            "confidence": "medium",
        },
    },
    # ── Score ~55: Borderline lead ───────────────────────────────────────
    {
        "conversation": (
            'MUSTERI: "Insaat sektorunde yatirimi dusunuyorum ama henuz net bir planım yok. '
            'Butcemizi belirlememiz lazim. Belki 1 yil icinde bir seyler yapariz."'
        ),
        "output": {
            "thinking": (
                "1. Yatirim dusunuyor ama 'net plan yok' — dusuk proje netligi. "
                "2. Karar verici pozisyonunda gorunuyor ('yatirimi dusunuyorum'). "
                "3. Butce belirlenmemis — 'belirlememiz lazim'. "
                "4. 'Belki 1 yil icinde' — uzun vadeli, belirsiz. "
                "5. ICP: henuz cok erken, ama potansiyel var. "
                "6. Kirmizi bayrak yok ama ciddiyet dusuk."
            ),
            "challenges_score": 8,
            "challenges_reasoning": "'Insaat sektorunde yatirimi dusunuyorum' — genel ilgi, somut proje yok",
            "challenges_confidence": 0.4,
            "authority_score": 12,
            "authority_reasoning": "Kendi yatirimi olarak bahsediyor — muhtemelen karar verici",
            "authority_confidence": 0.5,
            "money_score": 5,
            "money_reasoning": "'Butcemizi belirlememiz lazim' — butce henuz yok",
            "money_confidence": 0.3,
            "prioritization_score": 8,
            "prioritization_reasoning": "'Belki 1 yil icinde' — belirsiz, uzun vadeli",
            "prioritization_confidence": 0.4,
            "holistic_score": 55,
            "holistic_reasoning": "Potansiyel var ama erken asamada. Proje, butce ve zaman cizelgesi belirsiz. Takip edilmeli ama acil handoff gereksiz.",
            "icp_fit_assessment": "Dusuk uyum — henuz somut proje, butce ve zaman yok",
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
            "missing_info": ["Proje tipi ve kapsamı", "Butce araligi", "Arsa durumu", "Somut zaman cizelgesi"],
            "recommended_next_question": "Ne tur bir proje dusunuyorsunuz — konut mu, ticari mi?",
            "handoff_ready": False,
            "handoff_reason": "Erken asamada, proje ve butce belirsiz",
            "confidence": "low",
        },
    },
    # ── Score ~35: Low quality lead ──────────────────────────────────────
    {
        "conversation": (
            'MUSTERI: "Insaat maliyetleri ne kadar? Metrekare fiyatlari hakkinda bilgi almak istiyorum."'
        ),
        "output": {
            "thinking": (
                "1. Sadece maliyet soruyor — somut proje yok. "
                "2. Karar verici durumu bilinmiyor. "
                "3. Butce paylasmadan fiyat soruyor — price fishing riski. "
                "4. Zaman cizelgesi yok. "
                "5. ICP: cok dusuk uyum, genel bilgi talebi. "
                "6. Henuz tek mesaj, price fishing kesin degil ama erken sinyal."
            ),
            "challenges_score": 5,
            "challenges_reasoning": "'Insaat maliyetleri ne kadar' — genel bilgi talebi, proje yok",
            "challenges_confidence": 0.3,
            "authority_score": 0,
            "authority_reasoning": "Hicbir bilgi yok",
            "authority_confidence": 0.1,
            "money_score": 3,
            "money_reasoning": "Fiyat soruyor ama kendi butcesi hakkinda bilgi yok",
            "money_confidence": 0.2,
            "prioritization_score": 0,
            "prioritization_reasoning": "Zaman bilgisi yok",
            "prioritization_confidence": 0.1,
            "holistic_score": 35,
            "holistic_reasoning": "Genel bilgi talebi. Proje, butce, yetki ve zaman bilgisi eksik. Price fishing riski var ama henuz kesin degil. Daha fazla bilgi toplama gerekli.",
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
            "missing_info": ["Proje tipi", "Konum", "Butce", "Karar verici durumu", "Zaman cizelgesi"],
            "recommended_next_question": "Ne tur bir proje dusunuyorsunuz? Konut mu yoksa ticari bir proje mi?",
            "handoff_ready": False,
            "handoff_reason": "Yeterli bilgi yok, sohbet devam etmeli",
            "confidence": "low",
        },
    },
    # ── Score ~15: Disqualified / competitor ─────────────────────────────
    {
        "conversation": (
            'MUSTERI: "Biz de insaat sektorundeyiz, rakip firma olarak fiyatlarinizi merak ettik. '
            'Piyasa arastirmasi yapiyoruz."'
        ),
        "output": {
            "thinking": (
                "1. Acikca rakip firma oldugunu soyledi. "
                "2. 'Piyasa arastirmasi' — musteri degil, rakip istihbarati. "
                "3. Bu lead diskalifiye edilmeli. "
                "4. Hicbir CHAMP boyutu gecerli degil."
            ),
            "challenges_score": 0,
            "challenges_reasoning": "Rakip firma — gercek bir proje yok",
            "challenges_confidence": 0.9,
            "authority_score": 0,
            "authority_reasoning": "Musteri degil, rakip firma calisani",
            "authority_confidence": 0.9,
            "money_score": 0,
            "money_reasoning": "Alis butcesi yok — rakip istihbarati",
            "money_confidence": 0.9,
            "prioritization_score": 0,
            "prioritization_reasoning": "Proje yok, satin alma niyeti yok",
            "prioritization_confidence": 0.9,
            "holistic_score": 5,
            "holistic_reasoning": "Rakip firma — diskalifiye. Satis ekibinin zamani harcanmamali.",
            "icp_fit_assessment": "Uyumsuz — potansiyel musteri degil, rakip firma",
            "negative_signals": ["competitor"],
            "negative_penalty": -100,
            "negative_reasoning": "'Biz de insaat sektorundeyiz, rakip firma olarak' — acik rakip beyanı",
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
            "handoff_ready": False,
            "handoff_reason": "Rakip firma, yonlendirme gereksiz",
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
