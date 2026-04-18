from app.application.scoring.champ_extractor import _has_critical_missing_info


def test_critical_missing_info_does_not_block_on_segment_if_buyer_segment_known():
    lead = {
        "form_data": {
            "why_are_you_interested_in_north_cyprus?": "investment_and_rental_income",
        },
    }
    champ_json = {"missing_info": ["Segment teyidi"]}
    assert _has_critical_missing_info(champ_json, lead) is False


def test_critical_missing_info_treats_investor_model_as_critical_for_investor_lead():
    lead = {
        "buyer_segment": "investor",
    }
    champ_json = {"missing_info": ["Airbnb mi kira mi net degil"]}
    assert _has_critical_missing_info(champ_json, lead) is True
