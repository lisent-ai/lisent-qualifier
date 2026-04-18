"""
Turkish Cyprus real-estate few-shot anchors for qualification judge.

5 calibration examples spanning the full score range.
Domain: Cyprus Constructions - villas, apartments, resort residences.
"""
import json


JUDGE_EXAMPLES: list[dict] = [
    {
        "conversation": (
            'MUSTERI: "Esentepe tarafinda denize yakin bir villa bakiyorum. '
            'Bunu yatirim icin dusunuyorum; uygun projeyi erken alip teslime yakin satmak '
            'daha cok ilgimi cekiyor ama gerekiyorsa bir sure Airbnb tarafina da acigim. '
            'Butcem 700-900 bin euro, nakit hazir. Karar verici benim, esim gorus bildirir '
            'ama son karar bende. Bu yaz bitmeden ilerlemek istiyorum. Isterseniz online goruselim."'
        ),
        "output": {
            "thinking": (
                "1. Musteri gercek alici ve hedefi net. "
                "2. Segment acikca yatirimci; segmenti yeniden kesfetmeye gerek yok. "
                "3. Yatirim modeli guclu sekilde belli: ana plan erken alip teslime yakin satmak, ikincil olarak Airbnb acikligi var. "
                "4. 700-900K EUR nakit hazir - guclu butce ve finansman hazirligi. "
                "5. Karar verici net. "
                "6. Zamanlama net ve yakin. "
                "7. Online gorusme talebi var; handoff icin cok guclu sinyal."
            ),
            "challenges_score": 24,
            "challenges_reasoning": "Villa, Esentepe, erken al-sat odakli yatirim modeli net",
            "challenges_confidence": 0.95,
            "authority_score": 23,
            "authority_reasoning": "'Karar verici benim' diyerek net otorite verdi",
            "authority_confidence": 0.95,
            "money_score": 24,
            "money_reasoning": "700-900 bin euro ve nakit hazir bilgisi net butce hazirligi gosteriyor",
            "money_confidence": 0.95,
            "prioritization_score": 22,
            "prioritization_reasoning": "'Bu yaz bitmeden ilerlemek istiyorum' guclu zamanlama sinyali",
            "prioritization_confidence": 0.9,
            "holistic_score": 93,
            "holistic_reasoning": "Butce, otorite, zamanlama ve yatirim modeli guclu. Musteri gorusme de talep ediyor; bu lead satis ekibine hazir.",
            "icp_fit_assessment": "Tam uyum - ciddi yatirimci, net butce, net karar verici, yakin zamanlama",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": "nakit",
                "competing_bids": None,
                "project_sqm": None
            },
            "missing_info": ["Tercih edilen cikis plani detayi"],
            "recommended_next_question": "Bu tarafta agirlik daha cok teslimde cikmakta mi, yoksa 1-2 yil tutup kira veya Airbnb ile degerlendirmekte mi olacak?",
            "handoff_ready": True,
            "handoff_reason": "Lead ciddi, gorusme talep ediyor ve temel CHAMP boyutlari guclu",
            "confidence": "high",
            "extracted_budget_range": "under_500k",
            "extracted_budget_amount": 900000,
            "extracted_project_type": "residential",
            "extracted_timeline_urgency": "short",
            "extracted_decision_authority": "sole",
            "extracted_city": "Esentepe",
            "extracted_project_details": "villa yatirimi, erken al-sat odagi",
        },
    },
    {
        "conversation": (
            'MUSTERI: "Polonya\'da yasiyorum. Kibris\'ta denize yakin bir yer olsun istiyorum. '
            'Yazin ara ara ben de kullanayim, kullanmadigim donemlerde Airbnb de olabilir. '
            'Esimle beraber bakiyoruz. Butcemiz 260-340 bin euro civari. Bu yaz olmasa da yil icinde '
            'dogru bir sey bulursak ilerleyebiliriz."'
        ),
        "output": {
            "thinking": (
                "1. Musteri gercek alici ve tatil evi + gelir hibriti dusunuyor. "
                "2. Segment tekrar sorulmadan anlasilabilir. "
                "3. Butce araligi var ama finansman sekli net degil. "
                "4. Karar ortak. "
                "5. Zamanlama orta-yakin ama esnek. "
                "6. Kibris bilgisi ve kullanim donemi biraz daha netlesebilir. "
                "7. Handoff icin erken ama kaliteli lead."
            ),
            "challenges_score": 18,
            "challenges_reasoning": "Denize yakin tatil evi ve Airbnb hibriti istiyor; ihtiyac makul seviyede net",
            "challenges_confidence": 0.8,
            "authority_score": 14,
            "authority_reasoning": "'Esimle beraber bakiyoruz' ortak karar sinyali",
            "authority_confidence": 0.7,
            "money_score": 18,
            "money_reasoning": "260-340 bin euro araligi verildi ama finansman tipi net degil",
            "money_confidence": 0.8,
            "prioritization_score": 14,
            "prioritization_reasoning": "'Yil icinde dogru bir sey bulursak' orta aciliyet ve esneklik gosteriyor",
            "prioritization_confidence": 0.65,
            "holistic_score": 76,
            "holistic_reasoning": "Lead ciddi ve butce uyumlu gorunuyor; ancak Kibris hakimiyeti, kullanim donemi ve karar sureci biraz daha netlesmeli.",
            "icp_fit_assessment": "Iyi uyum - hem tatil evi hem gelir odagi var, butce uyumlu, karar sureci ortak",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None
            },
            "missing_info": ["Kullanim donemi", "Kibris hakimiyeti", "Finansman sekli"],
            "recommended_next_question": "Yilin daha cok hangi donemlerinde kullanmayi dusunuyorsunuz, yoksa agirlik daha cok kira / Airbnb tarafinda mi olacak?",
            "handoff_ready": False,
            "handoff_reason": "Lead iyi ama karar sureci ve kullanim detayi henuz netlesmedi",
            "confidence": "medium",
            "extracted_budget_range": "500k_1m",
            "extracted_budget_amount": 340000,
            "extracted_project_type": "residential",
            "extracted_timeline_urgency": "medium",
            "extracted_decision_authority": "joint",
            "extracted_city": "",
            "extracted_project_details": "denize yakin tatil evi, Airbnb opsiyonlu",
        },
    },
    {
        "conversation": (
            'MUSTERI: "Kibris\'ta yatirim dusunuyorum ama daha cok arastirma asamasindayim. '
            'Belki studio, belki 1+1, henuz net degil. Uygun bir proje bulursam bu yil olabilir. '
            'Fiyat bandiniz ve mantik nasilliyor onu anlamaya calisiyorum."'
        ),
        "output": {
            "thinking": (
                "1. Yatirim ilgisi var ama tip ve model belirsiz. "
                "2. Segment yatirimciya yakin ama tekrar sormak yerine yatirim modelini netlestirmek daha degerli. "
                "3. Butce yok. "
                "4. Zamanlama belirsiz ama bu yil olabilir sinyali var. "
                "5. Erken asama bir lead; bilgilendirme ile ilerletilebilir."
            ),
            "challenges_score": 8,
            "challenges_reasoning": "Studio veya 1+1 gibi genel ilgi var ama net unit tipi ve strateji yok",
            "challenges_confidence": 0.45,
            "authority_score": 10,
            "authority_reasoning": "Kendi adina konusuyor gibi gorunuyor ama karar yapisi net degil",
            "authority_confidence": 0.45,
            "money_score": 3,
            "money_reasoning": "Butce paylasilmadi",
            "money_confidence": 0.2,
            "prioritization_score": 9,
            "prioritization_reasoning": "'Bu yil olabilir' zayif ama faydali bir zamanlama sinyali",
            "prioritization_confidence": 0.4,
            "holistic_score": 57,
            "holistic_reasoning": "Potansiyel var ama erken asamada. Once yatirim modeli ve butce netlesmeli.",
            "icp_fit_assessment": "Orta-alt uyum - ilgi var ama net ciddiyet ve butce eksik",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None
            },
            "missing_info": ["Yatirim modeli", "Butce", "Karar sureci"],
            "recommended_next_question": "Bu tarafta daha cok projeyi erken alip teslimde satmak mi dusunuyorsunuz, yoksa kira / Airbnb gelirine donuk bir plan mi var?",
            "handoff_ready": False,
            "handoff_reason": "Erken asama; yatirim modeli ve butce netlesmeden handoff icin erken",
            "confidence": "low",
            "extracted_budget_range": "",
            "extracted_budget_amount": None,
            "extracted_project_type": "residential",
            "extracted_timeline_urgency": "medium",
            "extracted_decision_authority": "",
            "extracted_city": "",
            "extracted_project_details": "studio veya 1+1 yatirimi dusunuyor",
        },
    },
    {
        "conversation": (
            'MUSTERI: "Fiyatlar ne kadar? Studio kac para? 1+1 kac para? Bir liste var mi?"'
        ),
        "output": {
            "thinking": (
                "1. Sadece fiyat soruyor. "
                "2. Butce, zamanlama, karar verici ve kullanim amaci yok. "
                "3. Price fishing riski var ama tek tur oldugu icin kontrollu ceza gerekir. "
                "4. Once segmentten cok butce ve niyet sinyali toplamak gerekir."
            ),
            "challenges_score": 3,
            "challenges_reasoning": "Unit tipi sadece genel seviyede geciyor; gercek ihtiyac net degil",
            "challenges_confidence": 0.25,
            "authority_score": 0,
            "authority_reasoning": "Karar yapisi hakkinda hic bilgi yok",
            "authority_confidence": 0.1,
            "money_score": 2,
            "money_reasoning": "Kendi butcesini vermeden sadece fiyat soruyor",
            "money_confidence": 0.15,
            "prioritization_score": 0,
            "prioritization_reasoning": "Zamanlama bilgisi yok",
            "prioritization_confidence": 0.1,
            "holistic_score": 34,
            "holistic_reasoning": "Genel fiyat arastirmasi gibi gorunuyor. Daha fazla bilgi toplanmadan handoff yapilmaz.",
            "icp_fit_assessment": "Dusuk uyum - sadece fiyat odakli, butce ve ciddiyet yok",
            "negative_signals": ["price_fishing"],
            "negative_penalty": -10,
            "negative_reasoning": "Butce vermeden fiyat istiyor",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None
            },
            "missing_info": ["Butce", "Kullanim / yatirim amaci", "Zamanlama"],
            "recommended_next_question": "Daha net yonlendirebilmem icin bu tarafta nasil bir butce bandi dusunuyorsunuz?",
            "handoff_ready": False,
            "handoff_reason": "Yeterli kalifikasyon sinyali yok; sohbet devam etmeli",
            "confidence": "low",
            "extracted_budget_range": "",
            "extracted_budget_amount": None,
            "extracted_project_type": "residential",
            "extracted_timeline_urgency": "",
            "extracted_decision_authority": "",
            "extracted_city": "",
            "extracted_project_details": "genel fiyat arastirmasi",
        },
    },
    {
        "conversation": (
            'MUSTERI: "Kibris tarafini cok bilmiyorum. Neler var elinizde, siz yonlendirirseniz daha iyi olur. '
            'Yazin ara ara ben de kullanmak isterim ama para da getirsin. Henuz karar vermedim, once mantigini anlamak istiyorum."'
        ),
        "output": {
            "thinking": (
                "1. Gercek bir alici olma ihtimali var ama erken kesif asamasinda. "
                "2. Tatil evi + gelir hibriti dusunuyor; bu nedenle segmenti tekrar sormaya gerek yok. "
                "3. 'Siz yonlendirin' diyerek yeni qualification sorusundan once insan gibi cerceve ve onerinin verilmesini bekliyor. "
                "4. Butce ve karar yapisi eksik. "
                "5. 'Henuz karar vermedim' net fren sinyali; satin almaya geciyor gibi dusunulmemeli. "
                "6. Handoff icin erken."
            ),
            "challenges_score": 16,
            "challenges_reasoning": "Kullanim + gelir hibriti ihtiyaci makul olcude net",
            "challenges_confidence": 0.75,
            "authority_score": 8,
            "authority_reasoning": "Kendi adina konusuyor gibi ama karar sureci acik degil",
            "authority_confidence": 0.35,
            "money_score": 4,
            "money_reasoning": "Butce veya finansman hazirligi yok",
            "money_confidence": 0.2,
            "prioritization_score": 9,
            "prioritization_reasoning": "Yaz kullanimi sinyali var ama satin alma zamani belirsiz",
            "prioritization_confidence": 0.45,
            "holistic_score": 58,
            "holistic_reasoning": "Lead potansiyelli ama erken asamada; once guven veren yonlendirme ve kullanim senaryosu netligi lazim.",
            "icp_fit_assessment": "Orta uyum - ilgi var ama butce ve karar netligi eksik",
            "negative_signals": ["just_looking"],
            "negative_penalty": -4,
            "negative_reasoning": "'Henuz karar vermedim, once mantigini anlamak istiyorum' ifadesi erken arastirma sinyali",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None
            },
            "missing_info": ["Kullanim donemi", "Butce", "Karar sureci"],
            "recommended_next_question": "Yilin daha cok hangi donemlerinde kullanmayi dusunuyorsunuz, yoksa agirlik daha cok gelir tarafinda mi olacak?",
            "handoff_ready": False,
            "handoff_reason": "Erken kesif asamasi; once yonlendirme ve temel sinyaller netlesmeli",
            "confidence": "medium",
            "extracted_budget_range": "",
            "extracted_budget_amount": None,
            "extracted_project_type": "residential",
            "extracted_timeline_urgency": "medium",
            "extracted_decision_authority": "",
            "extracted_city": "",
            "extracted_project_details": "tatil evi + gelir hibriti, yonlendirme bekliyor",
        },
    },
    {
        "conversation": (
            'ASISTAN: "Isterseniz ornek dagilim ve odeme cercevesi hazirlayayim." '
            'MUSTERI: "Olur ama once bir dusuneyim, karar vermedim henuz."'
        ),
        "output": {
            "thinking": (
                "1. Kullanici materyal almaya acik ama satin alma karari vermis degil. "
                "2. 'Olur' burada devam izni; tek basina buying signal sayilmaz. "
                "3. 'Once bir dusuneyim' yumusak bekletme cumlesi. "
                "4. Bu durumda yeni qualification sorusu veya handoff zorlamak yanlis olur. "
                "5. Baskiyi dusurup kapiyi acik birakmak gerekir."
            ),
            "challenges_score": 10,
            "challenges_reasoning": "Ne aradigi bu kesitte ayrintili degil",
            "challenges_confidence": 0.35,
            "authority_score": 8,
            "authority_reasoning": "Karar sureci tek basina gibi gorunse de net degil",
            "authority_confidence": 0.25,
            "money_score": 6,
            "money_reasoning": "Odeme cercevesine ilgi var ama net finansman sinyali yok",
            "money_confidence": 0.3,
            "prioritization_score": 4,
            "prioritization_reasoning": "Beklemeyi tercih ediyor, yakin aksiyon sinyali yok",
            "prioritization_confidence": 0.3,
            "holistic_score": 43,
            "holistic_reasoning": "Ilgi suruyor ama musteri durup dusunmek istiyor; bu asamada handoff icin erken.",
            "icp_fit_assessment": "Belirsiz/erken - ilgi var ama momentum dusuk",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None
            },
            "missing_info": ["Temel ihtiyac", "Butce", "Zamanlama"],
            "recommended_next_question": "",
            "handoff_ready": False,
            "handoff_reason": "Musteri dusunmek icin beklemek istiyor; baskisiz sekilde devam edilmeli",
            "confidence": "medium",
            "extracted_budget_range": "",
            "extracted_budget_amount": None,
            "extracted_project_type": "residential",
            "extracted_timeline_urgency": "",
            "extracted_decision_authority": "",
            "extracted_city": "",
            "extracted_project_details": "ornek odeme cercevesine acik ama dusunme modunda",
        },
    },
    {
        "conversation": (
            'MUSTERI: "Biz de emlak sektorundeyiz, piyasayi karsilastiriyoruz. '
            'Aktif fiyat listenizi paylasir misiniz?"'
        ),
        "output": {
            "thinking": (
                "1. Musteri oldugunu soylemiyor; emlak sektorunde oldugunu acikca belirtiyor. "
                "2. Amac piyasa karsilastirmasi. "
                "3. Gercek satin alma niyeti yok; rakip / sektor arastirmasi gibi gorunuyor."
            ),
            "challenges_score": 0,
            "challenges_reasoning": "Gercek alim ihtiyaci yok",
            "challenges_confidence": 0.95,
            "authority_score": 0,
            "authority_reasoning": "Musteri degil, sektor profesyoneli",
            "authority_confidence": 0.95,
            "money_score": 0,
            "money_reasoning": "Alis butcesi yok",
            "money_confidence": 0.95,
            "prioritization_score": 0,
            "prioritization_reasoning": "Satin alma zamanlamasi yok",
            "prioritization_confidence": 0.95,
            "holistic_score": 5,
            "holistic_reasoning": "Rakip / sektor arastirmasi gibi gorunuyor; diskalifiye edilmeli.",
            "icp_fit_assessment": "Uyumsuz - potansiyel alici degil",
            "negative_signals": ["competitor"],
            "negative_penalty": -100,
            "negative_reasoning": "Acikca emlak sektorunde oldugunu ve karsilastirma yaptigini soyledi",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None
            },
            "missing_info": [],
            "recommended_next_question": "",
            "handoff_ready": False,
            "handoff_reason": "Rakip / sektor arastirmasi; handoff gereksiz",
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
    {
        "conversation": (
            'MUSTERI: "KKTC\'de ilk yatirimim olacak. '
            'Esentepe veya Girne tarafinda 1+1 ya da 2+1 bir sey dusunuyorum. '
            'Butcem 230-300 bin sterlin. '
            'Pesinat ve 2-3 yillik taksit benim icin onemli; Turkiye tarafindan kredi secenegi de varsa duymak isterim. '
            'Bu ay icinde uygun proje varsa detayli odeme planina bakip gorusme yapabiliriz."'
        ),
        "output": {
            "thinking": (
                "1. Musteri gercek alici gibi gorunuyor; ilk kez KKTC yatirimi yapacak ama net bir alis niyeti var. "
                "2. Segment yatirimci ve ayni zamanda rehberlik bekliyor. "
                "3. Butce araligi net, pesinat ve taksit yapisi aktif karar kriteri. "
                "4. Zamanlama yakin. "
                "5. Odeme plani ve gorusme istegi handoff icin guclu sinyal."
            ),
            "challenges_score": 18,
            "challenges_reasoning": "Esentepe veya Girne'de 1+1 / 2+1 yatirim dusunuyor; ihtiyac cercevesi yeterince net",
            "challenges_confidence": 0.8,
            "authority_score": 14,
            "authority_reasoning": "Kendi adina konusuyor ama ortak karar bilgisi net degil",
            "authority_confidence": 0.55,
            "money_score": 20,
            "money_reasoning": "230-300 bin sterlin butce verdi; pesinat, taksit ve kredi secenegi karar kriteri",
            "money_confidence": 0.9,
            "prioritization_score": 19,
            "prioritization_reasoning": "'Bu ay icinde' ifadesi yakin zamanlama ve aksiyon istegi gosteriyor",
            "prioritization_confidence": 0.85,
            "holistic_score": 82,
            "holistic_reasoning": "Lead ciddi, butce ve odeme yapisi net, odeme plani ve gorusme istegi var. Satis ekibine aktarima uygun.",
            "icp_fit_assessment": "Guclu uyum - ilk kez KKTC yatirimcisi ama net butce, yakin zamanlama ve proje/odeme ilgisi var",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": "mixed",
                "competing_bids": None,
                "project_sqm": None
            },
            "missing_info": ["Karar surecine baska biri dahil mi"],
            "recommended_next_question": "Bu tarafta karar sizin uzerinizden mi ilerliyor, yoksa aileden baska biri de surece dahil olacak mi?",
            "handoff_ready": True,
            "handoff_reason": "Lead odeme plani ve gorusme talebiyle birlikte net butce ve yakin zamanlama paylasti",
            "confidence": "high",
            "extracted_budget_range": "500k_1m",
            "extracted_budget_amount": 300000,
            "extracted_project_type": "residential",
            "extracted_timeline_urgency": "short",
            "extracted_decision_authority": "",
            "extracted_city": "Esentepe",
            "extracted_project_details": "ilk kez KKTC yatirimi, 1+1 veya 2+1, odeme plani odakli",
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
