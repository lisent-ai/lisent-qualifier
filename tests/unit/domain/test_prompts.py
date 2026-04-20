"""Unit tests for prompt builders (pure string functions)."""
import json
from app.domain.conversation.prompts import (
    build_chat_system_prompt,
    build_champ_extraction_prompt,
    build_reasoning_report_prompt,
    build_handoff_closing_prompt,
    build_qualification_judge_prompt,
)


def test_chat_prompt_unknown_cyprus_pushback_blocks_location_choice():
    prompt = build_chat_system_prompt(
        {"name": "Karolina"},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Girne'ye 20 dk mesafede, Çatalköy-Esentepe hattında denize yakın daire seçeneklerimiz bulunuyor. Hangi bölgeyi daha çok tercih edersiniz?"},
            {"role": "user", "content": "nasıl yani bilgim yok dedim"},
        ],
    )
    assert "erken bir bölge seçimine ittirdin" in prompt
    assert "lokasyon seçimi SORMA" in prompt
    assert "zamanlama veya kullanım amacı" in prompt


def test_chat_prompt_budget_reminder_with_deposit_question_answers_both_without_ratio_guess():
    prompt = build_chat_system_prompt(
        {
            "name": "Karolina",
            "budget_range": "£250.000 - £450.000",
            "form_data": {"what_is_your_budget_range?": "£250.000 - £450.000"},
        },
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "bütçem ne kadardı, peşinat için ne kadar lazım"}],
    )
    assert "formdaki bütçe bilgisini hatırlatmanı istiyor" in prompt
    assert "ikisini de cevapla" in prompt
    assert "genelde %20-30" in prompt
    assert "qualification sorusu sorma" in prompt


def test_chat_prompt_typo_confirmation_treated_as_permission():
    prompt = build_chat_system_prompt(
        {"name": "Karolina"},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Size uygun bir örnek planı netleştirip paylaşabilirim."},
            {"role": "user", "content": "olut"},
        ],
    )
    assert "teklif veya paylaşım için onay verdi" in prompt
    assert "olut" in prompt
    assert "başka onay isteme" in prompt


def test_chat_prompt_contains_lead_data():
    lead = {"contact": {"name": "Ali Veli"}, "project_type": "residential"}
    prompt = build_chat_system_prompt(lead)
    assert "Ali Veli" in prompt
    assert "residential" in prompt


def test_chat_prompt_includes_champ_section_when_provided():
    lead = {"contact": {"name": "Mehmet"}}
    champ = {"challenges_score": 20, "challenges_notes": "Villa inşaatı"}
    prompt = build_chat_system_prompt(lead, champ)
    assert "CHAMP" in prompt
    assert "Villa" in prompt


def test_chat_prompt_includes_gap_instruction():
    lead = {"contact": {"name": "Mehmet"}}
    champ = {"challenges_score": 20, "authority_score": 5, "money_score": 15, "prioritization_score": 10}
    prompt = build_chat_system_prompt(lead, champ)
    assert "authority" in prompt.lower()  # biggest gap should be mentioned


def test_chat_prompt_no_champ_section_when_absent():
    lead = {"contact": {"name": "Mehmet"}}
    prompt = build_chat_system_prompt(lead, None)
    assert "CHAMP Analysis" not in prompt


def test_chat_prompt_localizes_dynamic_sections_for_turkish():
    lead = {"contact": {"name": "Ayse"}}
    config = {
        "primary_language": "tr",
        "company_display_name": "Lisent",
        "custom_persona": "KKTC yatirim danismani",
        "forbidden_topics": ["vergi tavsiyesi"],
        "faq_entries": [{"question": "Odeme nasil?", "answer": "Pesinat + taksit olabilir."}],
        "custom_qualifying_questions": ["Daha cok yatirim icin mi bakiyorsunuz?"],
    }
    prompt = build_chat_system_prompt(lead, company_config=config)
    assert "Lisent ekibinde" in prompt
    assert "KKTC yatirim danismani" in prompt
    assert "[EK YASAK KONULAR]" in prompt
    assert "[ŞİRKET BİLGİ BANKASI - SSS]" in prompt
    assert "[ÖNCELİKLİ KALİFİKASYON SORULARI]" in prompt


def test_chat_prompt_includes_turn_guidance_for_polite_reply():
    lead = {"name": "Mehmet", "form_data": {"what_type_of_property_are_you_interested_in?": "4+1 villa"}}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "İyiyim siz nasılsınız"}],
    )
    assert "[BU TURDA NE YAPMALISIN]" in prompt
    assert "hal hatır / nezaket cevabı" in prompt
    assert "kapanış yapma" in prompt


def test_chat_prompt_includes_turn_guidance_for_company_question():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr", "company_display_name": "Cyprus Constructions"},
        messages=[{"role": "user", "content": "hangi şirketten yazıyordunuz?"}],
    )
    assert "hangi şirketten yazdığını soruyor" in prompt


def test_chat_prompt_includes_humanized_lead_summary():
    lead = {
        "name": "Mehmet",
        "project_type": "4+1_detached_villa_with_pool",
        "budget_range": "500000-900000 EUR",
        "form_data": {
            "why_are_you_interested_in_north_cyprus?": "investment_and_rental_income"
        },
    }
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
    )
    assert "İlgi Alanı: 4+1 havuzlu müstakil villa" in prompt
    assert "Amaç: yatırım ve kira getirisi" in prompt


def test_chat_prompt_includes_no_verified_knowledge_guard_when_kb_absent():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(lead, company_config={"primary_language": "tr"})
    assert "Şu anda doğrulanmış KB/RAG proje verisi yok." in prompt


def test_chat_prompt_uses_assistant_name_placeholder():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr", "assistant_name": "Gözde"},
    )
    assert "Senin adın Gözde." in prompt
    assert "ekibinde" not in prompt  # dynamic company context only appears if provided


def test_chat_prompt_includes_matched_projects_section_when_present():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={
            "primary_language": "tr",
            "matched_projects_section": "[BU LEAD İÇİN UYGUN PROJELER]\n## SEASIDE VILLAS",
            "kb_documents_content": "## SEASIDE VILLAS\n- Lokasyon: Esentepe",
        },
        messages=[{"role": "user", "content": "Seaside Villas hakkında biraz proje detayı verebilir misiniz?"}],
    )
    assert "[BU LEAD İÇİN UYGUN PROJELER]" in prompt
    assert "SEASIDE VILLAS" in prompt
    assert "[ŞİRKET BİLGİ BANKASI - DÖKÜMANLAR]" not in prompt


def test_chat_prompt_uses_simplified_lead_context():
    lead = {
        "name": "Mehmet",
        "project_type": "4+1_detached_villa_with_pool",
        "budget_range": "500000-900000 EUR",
        "notes": "investment_and_rental_income",
        "form_data": {"preferred_location": "Esentepe"},
    }
    prompt = build_chat_system_prompt(lead, company_config={"primary_language": "tr"})
    assert '"project_interest": "4+1 havuzlu müstakil villa"' in prompt
    assert '"preferred_location_signal": "Esentepe"' in prompt


def test_chat_prompt_mentions_message_splitting():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(lead, company_config={"primary_language": "tr"})
    assert "`---`" in prompt


def test_chat_prompt_includes_soft_reference_and_first_six_flow():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(lead, company_config={"primary_language": "tr"})
    assert "soft reference" in prompt.lower()
    assert "İlk 6 Mesaj Akışı" in prompt
    assert "`FirstTouch`" in prompt


def test_chat_prompt_does_not_push_price_when_user_only_wants_suggestions():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={
            "primary_language": "tr",
            "matched_projects_section": "[BU LEAD İÇİN UYGUN PROJELER]\n- En yakın proje: Seaside Villas",
        },
        messages=[{"role": "user", "content": "siz ne önerirsiniz"}],
    )
    assert "Müşteri fiyat sormadıysa fiyatı söyleme." in prompt
    assert "sadece EN UYGUN 1 projeyi söyle" in prompt


def test_chat_prompt_hides_matched_projects_during_discovery_turn():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={
            "primary_language": "tr",
            "matched_projects_section": "[BU LEAD İÇİN UYGUN PROJELER]\n- En yakın proje: Seaside Villas",
            "kb_documents_content": "## SEASIDE VILLAS\n- Lokasyon: Esentepe",
        },
        messages=[{"role": "user", "content": "yatırım için düşünüyorum ama yazın ben de kullanabilsem güzel olur"}],
    )
    assert "[BU LEAD İÇİN UYGUN PROJELER]" not in prompt
    assert "[ŞİRKET BİLGİ BANKASI - DÖKÜMANLAR]" not in prompt


def test_chat_prompt_shows_matched_projects_when_user_asks_project_details():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={
            "primary_language": "tr",
            "matched_projects_section": "[BU LEAD İÇİN UYGUN PROJELER]\n- En yakın proje: Seaside Villas",
        },
        messages=[{"role": "user", "content": "ne önerirsiniz, biraz proje bilgisi verebilir misiniz"}],
    )
    assert "[BU LEAD İÇİN UYGUN PROJELER]" in prompt


def test_chat_prompt_guides_investment_question_without_inventing_yield():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "nasıl yatırım yapabilirim, kira getirisi nasıl olur"}],
    )
    assert "net getiri oranı" in prompt
    assert "broşür, PDF, uzman ekip" in prompt


def test_chat_prompt_pauses_and_simplifies_when_user_is_confused():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "yani"}],
    )
    assert "[BU TURDA NE YAPMALISIN]" in prompt
    assert "DURDUR" in prompt


def test_chat_prompt_treats_ne_belgesi_as_confusion_not_material_request():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "ne belgesi"}],
    )
    assert "Kullanıcı son sorunu veya cümleni anlamadı." in prompt
    assert "Kıbrıs tarafını biraz araştırma fırsatınız oldu mu" in prompt


def test_chat_prompt_answers_project_ownership_before_qualification():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={
            "primary_language": "tr",
            "matched_projects_section": "[BU LEAD ICIN UYGUN PROJELER]\n- En yakin proje: Bahamas Homes 2",
            "kb_documents_content": "## BAHAMAS HOMES 2\n- Lokasyon: Esentepe",
        },
        messages=[{"role": "user", "content": "pearl island sizin mi"}],
    )
    assert "[BU TURDA NE YAPMALISIN]" in prompt
    assert "qualification sorusu sorma" in prompt
    assert "Bahamas Homes 2" in prompt


def test_chat_prompt_payment_question_stays_general_without_verified_terms():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "pesinat secenekleriniz nasil oluyor genelde"}],
    )
    assert "UYDURMA" in prompt
    assert "call iteleme" in prompt


def test_chat_prompt_price_question_requires_direct_verified_answer():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "fiyati ne kadar"}],
    )
    assert "Kullanıcı fiyat soruyor." in prompt
    assert "Bütçenize uygun" in prompt


def test_chat_prompt_facilities_question_answers_info_before_discovery():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "imkanlari ne tesisleri vs"}],
    )
    assert "tesis, imkan veya proje özelliklerini soruyor" in prompt
    assert "zamanlama sorusu ekleme" in prompt


def test_chat_prompt_link_request_does_not_promise_unverified_brochure():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "link var mi ya da brosur"}],
    )
    assert "hemen gönderiyorum" in prompt
    assert "handoff yapma" in prompt


def test_chat_prompt_handles_information_complaint_without_question():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "bilgi vermiyorsunuz hic"}],
    )
    assert "hafif hayal kırıklığı" in prompt
    assert "Bu turda soru sorma" in prompt


def test_chat_prompt_does_not_repeat_timing_when_user_already_gave_it():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "uygun fırsat olursa birkaç ay içinde alabilirim"}],
    )
    assert "Aynı turda zamanlamayı yeniden sorma." in prompt


def test_chat_prompt_moves_from_location_preference_to_timing_not_project():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Deniz kenarı bir konum sizin için daha mı çekici, yoksa şehir merkezine yakın bir yer mi?"},
            {"role": "user", "content": "deniz kenarı her zaman daha iyidir sanki"},
        ],
    )
    assert "HEMEN proje önermeye geçme" in prompt
    assert "zamanlama sorusuna geç" in prompt


def test_holiday_home_lead_polite_reply_prioritizes_cyprus_familiarity():
    lead = {
        "name": "Tomasz",
        "form_data": {
            "why_are_you_interested_in_north_cyprus?": "i'm_looking_for_a_holiday_home_/_summer_house",
            "what_type_of_property_are_you_interested_in?": "2+1_penthouse",
        },
    }
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "iyiyim sizler"}],
    )
    assert "tatil evi alıcısına yakın" in prompt
    assert "yatırım mı kullanım mı sorusunu ilk turda zorlama" in prompt
    assert "Kıbrıs tarafını biraz araştırıp araştırmadığını" in prompt


def test_polite_reply_does_not_repeat_purpose_if_opening_already_said_investment():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Merhaba Mehmet Bey, yatırım için villa tarafına baktığınızı gördüm. Nasılsınız?"},
            {"role": "user", "content": "iyi siz"},
        ],
    )
    assert "amaç sinyali zaten var veya konuşmaya girmiş durumda" in prompt
    assert "Kıbrıs tarafını biraz araştırıp araştırmadığını" in prompt


def test_holiday_home_location_reply_moves_to_usage_period_not_project():
    lead = {
        "name": "Tomasz",
        "form_data": {
            "why_are_you_interested_in_north_cyprus?": "i'm_looking_for_a_holiday_home_/_summer_house",
            "what_type_of_property_are_you_interested_in?": "2+1_penthouse",
        },
    }
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Daha çok deniz kenarı mı, merkeze yakın taraf mı size uyar?"},
            {"role": "user", "content": "deniz kenarı daha iyi olur"},
        ],
    )
    assert "tatil evi için bölge / yaşam tarzı tercihini söyledi" in prompt
    assert "yılın hangi dönemlerinde kullanmayı düşünüyorsunuz" in prompt
    assert "HEMEN proje önermeye geçme" in prompt


def test_holiday_home_discovery_hides_matched_projects_even_with_kb():
    lead = {
        "name": "Tomasz",
        "form_data": {
            "why_are_you_interested_in_north_cyprus?": "i'm_looking_for_a_holiday_home_/_summer_house",
            "what_type_of_property_are_you_interested_in?": "2+1_penthouse",
        },
    }
    prompt = build_chat_system_prompt(
        lead,
        company_config={
            "primary_language": "tr",
            "matched_projects_section": "[BU LEAD İÇİN UYGUN PROJELER]\n- En yakın proje: Kyrenia Heights",
            "kb_documents_content": "## KYRENIA HEIGHTS\n- Lokasyon: Girne",
        },
        messages=[{"role": "user", "content": "çok bilgim yok, bir iki kez geldim"}],
    )
    assert "[BU LEAD İÇİN UYGUN PROJELER]" not in prompt
    assert "[ŞİRKET BİLGİ BANKASI - DÖKÜMANLAR]" not in prompt


def test_chat_prompt_includes_buyer_segment_in_lead_summary():
    lead = {
        "name": "Tomasz",
        "form_data": {
            "why_are_you_interested_in_north_cyprus?": "i'm_looking_for_a_holiday_home_/_summer_house",
        },
    }
    prompt = build_chat_system_prompt(lead, company_config={"primary_language": "tr"})
    assert "Müşteri Tipi: tatil evi alıcısı" in prompt


def test_chat_prompt_prefers_explicit_buyer_segment_hint_from_company_config():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr", "buyer_segment": "investor"},
    )
    assert "lead formu + sohbetten gelen güçlü bir ön bilgi" in prompt
    assert "erken alıp teslime yakın satmak" in prompt
    assert "Airbnb yönetimi konuşulabilir" in prompt


def test_champ_extraction_prompt_contains_conversation():
    conv = "USER: Bütçem 2 milyon TL\nASSISTANT: Ne zaman başlamayı düşünüyorsunuz?"
    prompt = build_champ_extraction_prompt(conv)
    assert "2 milyon" in prompt
    assert "challenges_score" in prompt
    assert "confidence" in prompt


def test_champ_extraction_prompt_includes_few_shots():
    conv = "USER: Test"
    prompt = build_champ_extraction_prompt(conv)
    assert "Örnek" in prompt  # Turkish few-shot examples should be present


def test_reasoning_report_prompt_contains_score():
    lead = {"contact": {"name": "Test"}}
    breakdown = {"challenges": 25, "total": 85}
    prompt = build_reasoning_report_prompt(lead, 85, breakdown)
    assert "85" in prompt
    assert "summary" in prompt


def test_handoff_closing_prompt_is_nonempty():
    prompt = build_handoff_closing_prompt()
    assert len(prompt) > 10


def test_multilingual_templates_english():
    lead = {"contact": {"name": "John"}}
    config = {"primary_language": "en", "industry_focus": "construction"}
    prompt = build_chat_system_prompt(lead, company_config=config)
    assert "John" in prompt
    assert "CHAMP" in prompt
    assert "[KNOWLEDGE GUARD]" in prompt


def test_extraction_prompt_with_current_champ():
    conv = "USER: Test"
    current = {"challenges_score": 15, "authority_score": 10, "money_score": 0, "prioritization_score": 5}
    prompt = build_champ_extraction_prompt(conv, current_champ_json=current)
    assert "Mevcut CHAMP Durumu" in prompt
    assert "Değişmeyen boyutları mevcut skorlarında bırak." in prompt


def test_extraction_prompt_with_current_champ_in_english():
    conv = "USER: Test"
    current = {"challenges_score": 15, "authority_score": 10, "money_score": 0, "prioritization_score": 5}
    prompt = build_champ_extraction_prompt(
        conv,
        current_champ_json=current,
        company_config={"primary_language": "en", "industry_focus": "construction"},
    )
    assert "Current CHAMP State" in prompt
    assert "Keep unchanged dimensions at their current scores." in prompt


def test_judge_prompt_can_be_forced_to_english_without_company_config():
    prompt = build_qualification_judge_prompt(
        conversation_history="USER: I am looking for a villa.",
        lead_json={"contact": {"name": "John"}},
        language="en",
        sector="construction",
    )
    assert "## Current Assessment" not in prompt
    assert "You are an experienced construction industry lead qualification expert." in prompt


def test_turkish_judge_prompt_uses_cyprus_specific_investor_logic():
    prompt = build_qualification_judge_prompt(
        conversation_history="MUSTERI: Yatirim icin bakiyorum.",
        lead_json={"buyer_segment": "investor", "contact": {"name": "Mehmet"}},
        company_config={"primary_language": "tr", "company_display_name": "Cyprus Constructions"},
    )
    assert "erken alip teslimde satmak" in prompt
    assert "Airbnb yonetimi" in prompt
    assert "Bu alanlar bu use case'te ikincil" in prompt


def test_chat_prompt_guides_unknown_cyprus_user_with_simple_categories_first():
    lead = {"name": "Tomasz"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "polonyada yaşıyorum, kıbrıs hakkında fikrim yok"}],
    )
    assert "Çatalköy / Esentepe hattında denize yakın" in prompt
    assert "Girne tarafını gerekirse merkeze ulaşımın rahat olduğu kısa bir not gibi an" in prompt
    assert "seçim yaptırmaya çalışma" in prompt


def test_chat_prompt_moves_on_after_location_orientation_acknowledgement():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Hiç sorun değil 🙂 Çatalköy / Esentepe hattında denize yakın projelerimiz var. O taraf daha sakin ve tatil hissi güçlü."},
            {"role": "user", "content": "evet anladım"},
        ],
    )
    assert "Aynı lokasyon açıklamasını tekrar etme" in prompt
    assert "Girne mi Esentepe mi" in prompt
    assert "zamanlama, kullanım şekli veya bütçe esnekliği" in prompt


def test_chat_prompt_handles_rental_flexibility_question_without_inventing_management():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "ara ara kullanım nasıl oluyor, kiraya vermeyebilirim de"}],
    )
    assert "ara ara kullanım veya kiralama esnekliğini soruyor" in prompt
    assert "Kiralama zorunluymuş gibi konuşma" in prompt
    assert "yönetim firması" in prompt
    assert "tek kısa takip sorusu sor" in prompt


def test_chat_prompt_does_not_use_closing_language_after_acknowledging_investment_info():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Kiralama tamamen tercihe göre şekillenebilir; isterseniz kendi kullanımınıza da ayırabilirsiniz. Yatırım modeline göre değişir."},
            {"role": "user", "content": "anladım"},
        ],
    )
    assert "Ben de memnun oldum" in prompt
    assert "kapanış dili kullanma" in prompt
    assert "Tek kısa soru sor" in prompt


def test_chat_prompt_hides_matches_during_early_exploration_even_if_user_says_send():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={
            "primary_language": "tr",
            "matched_projects_section": "[BU LEAD İÇİN UYGUN PROJELER]\n- En yakın proje: Bahamas Homes 1",
            "kb_documents_content": "## BAHAMAS HOMES 1\n- Lokasyon: Esentepe",
        },
        messages=[
            {"role": "user", "content": "bakınıyorum henüz, daha bilgim yok"},
            {"role": "assistant", "content": "İsterseniz uygun bir örnek paylaşayım."},
            {"role": "user", "content": "olur gönderir misin"},
        ],
    )
    assert "[BU LEAD İÇİN UYGUN PROJELER]" not in prompt
    assert "[ŞİRKET BİLGİ BANKASI - DÖKÜMANLAR]" not in prompt


def test_chat_prompt_visual_request_does_not_promise_unverified_images():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "resim gönderebilir misin"}],
    )
    assert "Kullanıcı görsel, resim veya fotoğraf istiyor." in prompt
    assert "şimdi gönderiyorum" in prompt
    assert "Bu turda qualification sorusu sorma." in prompt


def test_chat_prompt_proximity_question_uses_verified_distance_only():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "denize ne kadar, havaalanına uzak mı"}],
    )
    assert "Kullanıcı yakınlık, uzaklık veya mesafe soruyor." in prompt
    assert "temiz ve doğrulanmış görünen mesafe verilerini kullan" in prompt


def test_chat_prompt_unit_detail_question_uses_property_description_only():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "teras var mı, manzarası nasıl"}],
    )
    assert "Kullanıcı ünitenin yaşam detaylarını soruyor." in prompt
    assert "property description içinde açıkça geçen" in prompt


def test_chat_prompt_does_not_reask_form_data_when_user_points_to_form():
    lead = {
        "name": "Mehmet",
        "budget_range": "500.000 - 900.000 €",
        "form_data": {"what_is_your_budget_range?": "500.000 - 900.000 €"},
    }
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "belirtmiştim formda, formdaki bütçem kaçtı?"}],
    )
    assert "formda" in prompt
    assert "formdaki net bütçe sinyali" in prompt
    assert "aynı bilgiyi tekrar isteme" in prompt


def test_chat_prompt_answers_remote_airbnb_management_bundle_before_quote_language():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[
            {
                "role": "user",
                "content": "studio daire almak istesem 3 tane mantıklı olur mu airbnb falan polonyada yaşıyorum ama hizmetiniz var mı yönetim",
            }
        ],
    )
    assert "Airbnb" in prompt
    assert "TÜM sorularını sırayla cevapla" in prompt
    assert "teklif" in prompt


def test_chat_prompt_treats_offer_confirmation_as_permission_not_purchase_commitment():
    lead = {
        "name": "Mehmet",
        "contact": {"email": "mehmet@example.com"},
    }
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "İsterseniz teklif ve ödeme planını hazırlayıp paylaşayım."},
            {"role": "user", "content": "olur isterim"},
        ],
    )
    assert "teklif" in prompt
    assert "onay verdi" in prompt
    assert "e-posta veya telefonu yeniden isteme" in prompt


def test_chat_prompt_respects_existing_contact_data_when_user_says_you_have_it():
    lead = {
        "name": "Mehmet",
        "contact": {"email": "mehmet@example.com", "phone": "+905551112233"},
    }
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Teklifin ulaşması için en uygun e-posta adresinizi paylaşır mısınız?"},
            {"role": "user", "content": "e bu bilgiler var sizde"},
        ],
    )
    assert "zaten sizde" in prompt
    assert "aynı bilgiyi tekrar isteme" in prompt
    assert "formdaki mevcut kanaldan paylaşabileceğini söyle" in prompt


def test_chat_prompt_deescalates_when_user_says_they_are_only_gathering_info():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "almaya dair karar verdiğimi söylemedim, bilgi alıyorum ha"}],
    )
    assert "bilgi" in prompt
    assert "varsayımı geri çek" in prompt
    assert "teklif" in prompt


def test_chat_prompt_investor_no_move_in_signal_blocks_move_in_question():
    lead = {"name": "Mehmet"}
    prompt = build_chat_system_prompt(
        lead,
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "airbnb yapacağız, taşınmayacağım"}],
    )
    assert "oturmak için değil" in prompt
    assert "yaşam senaryosu sorma" in prompt
def test_chat_prompt_includes_default_sales_playbook_sections():
    prompt = build_chat_system_prompt({"name": "Mehmet"}, company_config={"primary_language": "tr"})
    assert "[MARKA TONU VE GUVEN]" in prompt
    assert "[ONCELIKLI HEDEF SEGMENTLER]" in prompt
    assert "KKTC" in prompt
    assert "Airbnb" in prompt
    assert "garanti" in prompt


def test_chat_prompt_uses_custom_sales_playbook_over_defaults():
    prompt = build_chat_system_prompt(
        {"name": "Mehmet"},
        company_config={
            "primary_language": "tr",
            "priority_target_segments": ["Kurumsal yatirimci"],
            "brand_tone_notes": ["Daha analitik"],
            "trust_building_phrases": ["Rakamlari netlestirip ilerleyebiliriz."],
            "prohibited_phrases": ["kesin olur"],
        },
    )
    assert "Kurumsal yatirimci" in prompt
    assert "Daha analitik" in prompt
    assert "Rakamlari netlestirip ilerleyebiliriz." in prompt
    assert "kesin olur" in prompt


def test_judge_prompt_includes_sales_playbook_sections():
    prompt = build_qualification_judge_prompt(
        conversation_history="MUSTERI: Odeme plani ve pesinat benim icin onemli.",
        lead_json={"contact": {"name": "Mehmet"}},
        company_config={"primary_language": "tr"},
    )
    assert "SATIS PLAYBOOK'U - HANDOFF SINYALLERI" in prompt
    assert "odeme plani" in prompt.lower()
    assert "ilk kez KKTC yatirimi" in prompt or "ilk kez KKTC yatırımı" in prompt
