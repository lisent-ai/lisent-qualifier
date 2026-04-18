from app.domain.conversation.prompts import (
    build_chat_system_prompt,
    build_handoff_closing_prompt,
    build_qualification_judge_prompt,
    resolve_buyer_segment,
)
from app.application.conversation.handler import (
    _is_accepting_human_followup,
    _message_requires_reply_before_handoff,
)
from app.application.scoring.champ_extractor import _last_user_message_blocks_auto_handoff
from app.domain.scoring.signals.handoff_triggers import check_conversation_end
from app.domain.scoring.signals.languages.tr import TurkishSentimentAnalyzer
from app.application.whatsapp.handler import _split_whatsapp_parts


def test_soft_pause_is_not_treated_as_conversation_end_in_turkish():
    should_end, reason = check_conversation_end("Ben dusuneyim, sonra yazarim", language="tr")
    assert should_end is False
    assert reason == ""


def test_explicit_goodbye_is_still_conversation_end_in_turkish():
    should_end, reason = check_conversation_end("Tamam tesekkurler, iyi gunler", language="tr")
    assert should_end is True
    assert reason.startswith("conversation_end:")


def test_soft_pause_is_not_negative_sentiment_in_turkish_signal_rules():
    result = TurkishSentimentAnalyzer().analyze("Bir dusuneyim, sonra bakariz")
    assert result.label == "neutral"


def test_english_chat_prompt_includes_existing_data_and_info_mode_guards():
    prompt = build_chat_system_prompt(
        {"contact": {"name": "Alex", "email": "alex@example.com"}},
        company_config={"primary_language": "en", "industry_focus": "construction"},
    )
    assert "If the customer asks multiple questions in one message, answer ALL of them" in prompt
    assert "do not ask for the same data again" in prompt
    assert "they are only gathering information" in prompt


def test_turkish_judge_prompt_mentions_soft_pause_and_existing_data_rules():
    prompt = build_qualification_judge_prompt(
        conversation_history="MUSTERI: Karar vermedim, bilgi aliyorum.",
        lead_json={"contact": {"name": "Mehmet", "email": "mehmet@example.com"}},
        company_config={"primary_language": "tr", "company_display_name": "Cyprus Constructions"},
    )
    assert "Lead formunda veya CRM tarafinda zaten gorunen" in prompt
    assert "devam izni olabilir" in prompt
    assert "yumusak bekletme cumleleri" in prompt


def test_handoff_closing_prompt_avoids_robotic_specialist_language_in_instructions():
    prompt = build_handoff_closing_prompt(company_config={"primary_language": "tr"})
    assert '"uzman arkadasim", "uzman ekip", "musteri hizmetleri" gibi robotik' in prompt
    assert "ilgili danisman" in prompt


def test_soft_pause_after_material_offer_keeps_low_pressure():
    prompt = build_chat_system_prompt(
        {"name": "Mehmet", "contact": {"email": "mehmet@example.com"}},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Isterseniz ornek dagilimi ve odeme cercevesini paylasayim."},
            {"role": "user", "content": "olur ama once dusuneyim"},
        ],
    )
    assert "yumusak" in prompt
    assert "Baskiyi dusur" in prompt or "Baskıyı düşür" in prompt
    assert "aynı onayı tekrar isteme" in prompt or "qualification" in prompt


def test_turkish_chat_prompt_guides_recommendation_instead_of_bouncing_back():
    prompt = build_chat_system_prompt(
        {"name": "Mehmet"},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Ara ara kullanım ve gelir dengesi için birkaç seçenek konuşabiliriz."},
            {"role": "user", "content": "siz hangisini onerirsiniz, iki konu hakkinda da bilgi isterim"},
        ],
    )
    assert "Kullanıcı senden net öneri istiyor" in prompt
    assert "Topu tekrar kullanıcıya atma" in prompt
    assert "ikinci bir konu da açtıysa" in prompt


def test_turkish_chat_prompt_uses_category_frame_for_broad_project_request():
    prompt = build_chat_system_prompt(
        {"name": "Mehmet"},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Kıbrıs tarafını çok araştırma fırsatınız oldu mu?"},
            {"role": "user", "content": "neler var elinizde, siz yonlendirirseniz daha iyi olur"},
        ],
    )
    assert "2-3 anlaşılır kategoriyle yön ver" in prompt
    assert "hemen tek proje ismi fırlatma" in prompt


def test_open_question_blocks_force_handoff_safety_net():
    assert _message_requires_reply_before_handoff("neler var elinizde, siz yönlendirir misiniz?", "tr") is True
    assert _message_requires_reply_before_handoff("tamam tesekkurler iyi gunler", "tr") is False


def test_turkish_judge_few_shots_cover_guidance_request_and_soft_pause():
    prompt = build_qualification_judge_prompt(
        conversation_history="MUSTERI: neler var elinizde?",
        lead_json={"contact": {"name": "Mehmet"}},
        company_config={"primary_language": "tr", "company_display_name": "Cyprus Constructions"},
    )
    assert "siz yonlendirirseniz daha iyi olur" in prompt
    assert "Olur ama once bir dusuneyim" in prompt or "once bir dusuneyim" in prompt


def test_contextual_followup_acceptance_triggers_handoff_readiness():
    previous_assistant = "Isterseniz ekipteki ilgili danismanimiz bu tarafta sizinle devam etsin, kisa bir gorusme de yapabiliriz."
    assert _is_accepting_human_followup("olur yarin ogleden sonra da uyar", previous_assistant, "tr") is True
    assert _is_accepting_human_followup("simdilik bilgi aliyorum", previous_assistant, "tr") is False


def test_turkish_chat_prompt_admits_wrong_assumption_without_fake_history():
    prompt = build_chat_system_prompt(
        {"name": "David"},
        company_config={"primary_language": "tr"},
        messages=[{"role": "user", "content": "esim oldugu bilgisini nereden aldin?"}],
    )
    assert "CRM notu" in prompt
    assert "UYDURMA" in prompt


def test_turkish_chat_prompt_stops_schedule_ping_pong_after_human_acceptance():
    prompt = build_chat_system_prompt(
        {"name": "David", "contact": {"phone": "+905551112233"}},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Isterseniz bu tarafta ilgili satis danismanimiz sizinle devam etsin, kisa bir gorusme de yapabiliriz."},
            {"role": "user", "content": "olur yarin ogleden sonra"},
        ],
    )
    assert "insanla devam etme fikrine onay verdi" in prompt
    assert "sekreter gibi" in prompt
    assert "telefonu yeniden isteme" in prompt


def test_turkish_chat_prompt_keeps_living_flow_concrete_after_remote_work_signal():
    prompt = build_chat_system_prompt(
        {"name": "David"},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Nasıl bir kullanım düşünüyorsunuz?"},
            {"role": "user", "content": "kendim yaşamayı düşünüyorum, işlerimi uzaktan yapıyorum"},
        ],
    )
    assert "Airbnb" in prompt
    assert "3+1" in prompt
    assert "4+1" in prompt
    assert "site" in prompt


def test_turkish_chat_prompt_answers_size_question_without_seasonal_pivot():
    prompt = build_chat_system_prompt(
        {"name": "David"},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Denize yakın seçenekler konuşabiliriz."},
            {"role": "user", "content": "buyukluk konusunda secenekler nasil"},
        ],
    )
    assert "Airbnb" in prompt
    assert "3+1" in prompt
    assert "4+1" in prompt


def test_turkish_chat_prompt_acknowledges_positive_cyprus_impression_naturally():
    prompt = build_chat_system_prompt(
        {"name": "David"},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Kıbrıs tarafını biraz araştırma fırsatınız oldu mu?"},
            {"role": "user", "content": "pek yapamadım ama birkaç kez tatile geldik, huzur dolu denizi çok güzeldi"},
        ],
    )
    assert "Kullanıcı Kıbrıs'la ilgili iyi bir ilk izlenim paylaşıyor" in prompt
    assert "Esentepe / Çatalköy" in prompt
    assert "sert qualification" in prompt


def test_turkish_chat_prompt_does_not_force_narrow_unit_choice_for_budget_broad_request():
    prompt = build_chat_system_prompt(
        {"name": "David"},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Birkaç seçenek olabilir."},
            {"role": "user", "content": "bütçeme göre neler var bana sunabileceğiniz"},
        ],
    )
    assert "2-3 anlaşılır kategoriyle yön ver" in prompt
    assert "'2+1 mi 3+1 mi' diye daraltma" in prompt


def test_turkish_chat_prompt_answers_location_comparison_without_forced_choice():
    prompt = build_chat_system_prompt(
        {"name": "David"},
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "2+1 ve 3+1 tarafı var."},
            {"role": "user", "content": "ikisinin de konumu aynı mı hangisinin konumu daha iyi"},
        ],
    )
    assert "Kullanıcı konum karşılaştırması soruyor" in prompt
    assert "yeni seçim sorusu ekleme" in prompt


def test_whatsapp_splitter_turns_paragraphs_into_multiple_messages():
    parts = _split_whatsapp_parts("Merhaba David Bey.\n\nEsentepe tarafı sakin ve denize yakın.\n\nNasıl bir şey düşünüyorsunuz?")
    assert len(parts) >= 2
    assert parts[0].startswith("Merhaba David Bey.")


def test_active_info_request_blocks_auto_handoff_but_human_followup_does_not():
    class Msg:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    assert _last_user_message_blocks_auto_handoff(
        [Msg("assistant", "x"), Msg("user", "konum atabilir misin hangi bölgede önerdiğin projeler?")]
    ) is True
    assert _last_user_message_blocks_auto_handoff(
        [Msg("assistant", "x"), Msg("user", "olur görüşelim")]
    ) is False


def test_turkish_chat_system_prompt_bans_dramatic_first_message_language():
    prompt = build_chat_system_prompt(
        {"name": "David"},
        company_config={"primary_language": "tr"},
    )
    assert "Form cevabını dramatize etme" in prompt or "Form cevabini dramatize etme" in prompt
    assert "Deniz kenarında yeni bir hayat hayal ettiğinizi gördüm" in prompt or "Deniz kenarinda yeni bir hayat hayal ettiginizi gordum" in prompt


def test_residence_segment_detects_new_life_by_the_sea_form_value():
    segment = resolve_buyer_segment(
        {
            "form_data": {
                "why_are_you_interested_in_north_cyprus?": "i_want_to_start_a_new_life_by_the_sea"
            }
        },
        language="tr",
    )
    assert segment == "residence"


def test_turkish_chat_prompt_uses_residence_flow_when_form_already_signals_living():
    prompt = build_chat_system_prompt(
        {
            "name": "David",
            "form_data": {
                "why_are_you_interested_in_north_cyprus?": "i_want_to_start_a_new_life_by_the_sea"
            },
        },
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Ben de iyiyim, teşekkürler."},
            {"role": "user", "content": "iyiyim sizler"},
        ],
    )
    assert "Bu lead yaşam / oturum tarafına yakın görünüyor" in prompt
    assert "Airbnb, kira getirisi veya uzaktan yönetim diline sapma" in prompt


def test_segment_specific_cyprus_unknown_guidance_differs_by_buyer_type():
    residence_prompt = build_chat_system_prompt(
        {
            "name": "David",
            "form_data": {
                "why_are_you_interested_in_north_cyprus?": "i_want_to_start_a_new_life_by_the_sea"
            },
        },
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Kıbrıs tarafını biraz araştırma fırsatınız oldu mu?"},
            {"role": "user", "content": "yok pek bilmiyorum"},
        ],
    )
    holiday_prompt = build_chat_system_prompt(
        {
            "name": "Maria",
            "form_data": {
                "why_are_you_interested_in_north_cyprus?": "i'm_looking_for_a_holiday_home_/_summer_house"
            },
        },
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Kıbrıs tarafını biraz araştırma fırsatınız oldu mu?"},
            {"role": "user", "content": "yok pek bilmiyorum"},
        ],
    )
    investor_prompt = build_chat_system_prompt(
        {
            "name": "Mehmet",
            "form_data": {
                "why_are_you_interested_in_north_cyprus?": "investment_and_rental_income"
            },
        },
        company_config={"primary_language": "tr"},
        messages=[
            {"role": "assistant", "content": "Kıbrıs tarafını biraz araştırma fırsatınız oldu mu?"},
            {"role": "user", "content": "yok pek bilmiyorum"},
        ],
    )

    assert "taşınma zamanına ya da günlük yaşamda en önemli önceliğe geç" in residence_prompt
    assert "kullanım dönemi" in holiday_prompt
    assert "yatırım modeline veya zamanlamaya geç" in investor_prompt


def test_lead_summary_humanizes_new_life_phrase_into_residence_language():
    prompt = build_chat_system_prompt(
        {
            "name": "David",
            "form_data": {
                "why_are_you_interested_in_north_cyprus?": "i_want_to_start_a_new_life_by_the_sea"
            },
        },
        company_config={"primary_language": "tr"},
    )
    assert "Amaç: kendisi yaşamak / taşınmak için" in prompt
    assert "i_want_to_start_a_new_life_by_the_sea" not in prompt
