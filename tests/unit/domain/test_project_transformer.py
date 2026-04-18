from app.domain.knowledge.project_transformer import (
    build_rag_matched_section,
    transform_rag_portfolio,
)


def test_build_rag_matched_section_uses_recent_messages_to_shift_match():
    lead = {
        "project_type": "villa",
        "form_data": {
            "why_are_you_interested_in_north_cyprus?": "investment_and_rental_income",
        },
    }
    projects = {
        "data": [
            {
                "metadata": {
                    "id": "villa-project",
                    "name": "Bahamas Homes 1",
                    "location": '{"location":"Esentepe, Girne District, Northern Cyprus"}',
                    "bio": "Villa living with sea view and private pool.",
                }
            },
            {
                "metadata": {
                    "id": "studio-project",
                    "name": "Bahamas Homes 2",
                    "location": '{"location":"Esentepe, Girne District, Northern Cyprus"}',
                    "bio": "Studio and penthouse units near the sea for lifestyle and investment buyers.",
                }
            },
        ]
    }
    properties = {
        "data": [
            {
                "metadata": {
                    "projectId": "villa-project",
                    "priceFrom": "850000",
                    "description": "Bahamas Homes – 4+1 Villa offers private pool living by the sea.",
                }
            },
            {
                "metadata": {
                    "projectId": "studio-project",
                    "priceFrom": "171250",
                    "description": "Bahamas Homes – Garden Studio Apartment offers smart resort living.",
                }
            },
            {
                "metadata": {
                    "projectId": "studio-project",
                    "priceFrom": "195000",
                    "description": "Bahamas Homes – Penthouse Studio offers rooftop lifestyle and sea views.",
                }
            },
        ]
    }

    section = build_rag_matched_section(
        lead,
        projects,
        properties,
        language="tr",
        messages=[{"role": "user", "content": "villa yerine studio da düşünebilirim"}],
    )

    assert "Bahamas Homes 2" in section
    assert "Tip / fiyat özeti" in section
    assert "Doğal kısa özet" in section


def test_build_rag_matched_section_includes_constraints_for_properties_and_links():
    lead = {"project_type": "studio"}
    projects = {
        "data": [
            {
                "metadata": {
                    "id": "studio-project",
                    "name": "Bahamas Homes 2",
                    "location": '{"location":"Esentepe, Girne District, Northern Cyprus"}',
                    "bio": "Studio and penthouse units near the sea with communal pool.",
                }
            },
        ]
    }
    properties = {
        "data": [
            {
                "metadata": {
                    "projectId": "studio-project",
                    "priceFrom": "171250",
                    "description": "Bahamas Homes – Garden Studio Apartment offers smart resort living.",
                }
            },
        ]
    }

    section = build_rag_matched_section(lead, projects, properties, language="tr")

    assert "hangi üniteler var" in section
    assert "link UYDURMA" in section
    assert "kontrol edip paylaşayım" in section


def test_transform_rag_portfolio_uses_clean_distances_land_size_and_unit_notes():
    projects = {
        "data": [
            {
                "metadata": {
                    "id": "studio-project",
                    "name": "Bahamas Homes 2",
                    "projectCode": "BAHMAS-2026",
                    "location": '{"location":"Kalogreia, Çatalköy-Esentepe Belediyesi, Girne District, Northern Cyprus"}',
                    "bio": "Studio and penthouse units near the sea with communal pool and spa.",
                    "otherInfo": '{"exactAreaName":"Esentepe","landSize":"24139.42","deliveryDate":"2026-02","distanceSea":"5 min","distanceCity":"20 min","distanceAirport":"45 hr","distanceUniversity":"20 min"}',
                }
            },
        ]
    }
    properties = {
        "data": [
            {
                "metadata": {
                    "projectId": "studio-project",
                    "priceFrom": "171250",
                    "description": "Bahamas Homes – Penthouse Studio offers a rooftop terrace with panoramic sea view.",
                }
            },
        ]
    }

    section = transform_rag_portfolio(projects, properties, language="tr")

    assert "Bölge: Esentepe" in section
    assert "Arsa / Proje Alanı: 24,139 m²" in section
    assert "Denize: 5 dk" in section
    assert "Üniversiteye: 20 dk" in section
    assert "45 hr" not in section
    assert "Ünite Notları" in section
    assert "çatı terası" in section


def test_build_rag_matched_section_includes_delivery_and_clean_distance_details():
    lead = {"project_type": "studio"}
    projects = {
        "data": [
            {
                "metadata": {
                    "id": "studio-project",
                    "name": "Bahamas Homes 2",
                    "location": '{"location":"Esentepe, Girne District, Northern Cyprus"}',
                    "bio": "Studio and penthouse units near the sea with communal pool.",
                    "otherInfo": '{"exactAreaName":"Esentepe","landSize":"24139.42","deliveryDate":"2026-02","distanceSea":"5 min","distanceCity":"20 min","distanceAirport":"45 hr"}',
                }
            },
        ]
    }
    properties = {
        "data": [
            {
                "metadata": {
                    "projectId": "studio-project",
                    "priceFrom": "171250",
                    "description": "Bahamas Homes – Penthouse Studio offers a rooftop terrace with panoramic sea view.",
                }
            },
        ]
    }

    section = build_rag_matched_section(lead, projects, properties, language="tr")

    assert "Teslim: 2026-02" in section
    assert "Yakınlık: Denize: 5 dk | Şehre: 20 dk" in section
    assert "45 hr" not in section
