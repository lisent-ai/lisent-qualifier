"""
English Cyprus real-estate investor few-shot anchors for qualification judge.

4 calibration examples covering all 3 CTA bands.
"""
import json

JUDGE_EXAMPLES: list[dict] = [
    {
        "conversation": (
            'CUSTOMER: "Looking at a sea-view villa on the Esentepe side. '
            'Investment purpose, I\'ll likely put it on Airbnb. '
            'Budget 700-900k EUR, cash ready. I\'m the decision maker. '
            'I want to come over to Cyprus this summer and see it in person; '
            'happy to be your guest for a couple of days."'
        ),
        "output": {
            "thinking": (
                "1. Real buyer, target clear. "
                "2. Investor segment with Airbnb exit. "
                "3. 700-900k EUR cash ready - strong budget. "
                "4. Sole decision maker. "
                "5. Explicit intent to visit Cyprus in summer, happy to be a guest. "
                "6. Holistic high; perfect fit for cyprus_visit CTA."
            ),
            "challenges_score": 23,
            "challenges_reasoning": "Esentepe villa, Airbnb exit, investment model clear",
            "challenges_confidence": 0.9,
            "authority_score": 24,
            "authority_reasoning": "Sole decision maker",
            "authority_confidence": 0.95,
            "money_score": 24,
            "money_reasoning": "700-900k EUR cash ready",
            "money_confidence": 0.95,
            "prioritization_score": 22,
            "prioritization_reasoning": "Summer visit intent explicit",
            "prioritization_confidence": 0.9,
            "holistic_score": 92,
            "holistic_reasoning": "Ready for handoff; explicit visit intent.",
            "icp_fit_assessment": "Excellent fit",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": "cash",
                "visit_intent": True,
                "decision_partner_aligned": True,
                "exit_strategy_clear": True,
                "property_type": "villa",
                "location": "esentepe",
            },
            "missing_info": [],
            "recommended_next_question": "",
            "handoff_ready": True,
            "handoff_reason": "Budget + visit intent + sole authority → cyprus_visit",
            "cta_recommendation": "cyprus_visit",
            "confidence": "high",
            "extracted_budget_range": "500k_1m",
            "extracted_budget_amount": 900000,
            "extracted_project_type": "residential_investor",
            "extracted_timeline_urgency": "short",
            "extracted_decision_authority": "sole",
            "extracted_city": "Esentepe",
            "extracted_project_details": "villa investment, Airbnb focus, in-person visit intent",
        },
    },
    {
        "conversation": (
            'CUSTOMER: "Looking for an investment in Cyprus, probably a studio. '
            'Budget around 150-200k GBP, likely mortgage + deposit. '
            'I could put it on Airbnb at different times of year. '
            'My wife and I will decide together. I\'d like to understand how the process works."'
        ),
        "output": {
            "thinking": (
                "1. Serious investor, studio + Airbnb exit defined. "
                "2. Budget given, financing pending. "
                "3. Joint decision - needs clarification. "
                "4. No explicit visit intent, but engaged. "
                "5. Ready for online process walk-through; visit too early. "
                "6. Calendly suits - 30-min online meeting with Redif."
            ),
            "challenges_score": 17,
            "challenges_reasoning": "Studio + Airbnb, clear exit",
            "challenges_confidence": 0.8,
            "authority_score": 14,
            "authority_reasoning": "Joint decision, clarity needed",
            "authority_confidence": 0.7,
            "money_score": 15,
            "money_reasoning": "Budget range given, financing pending",
            "money_confidence": 0.7,
            "prioritization_score": 12,
            "prioritization_reasoning": "Process understanding, no near timing",
            "prioritization_confidence": 0.55,
            "holistic_score": 62,
            "holistic_reasoning": "Engaged serious lead; online meeting ideal to clarify financing and process.",
            "icp_fit_assessment": "Upper-mid fit",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": "pending",
                "visit_intent": False,
                "decision_partner_aligned": False,
                "exit_strategy_clear": True,
                "property_type": "studio",
            },
            "missing_info": ["Visit intent", "Financing detail", "Joint decision clarity"],
            "recommended_next_question": "",
            "handoff_ready": True,
            "handoff_reason": "Mature enough for detail + online meeting; calendly suits",
            "cta_recommendation": "calendly",
            "confidence": "medium",
            "extracted_budget_range": "under_500k",
            "extracted_budget_amount": 200000,
            "extracted_project_type": "residential_investor",
            "extracted_timeline_urgency": "medium",
            "extracted_decision_authority": "joint",
            "extracted_city": "",
            "extracted_project_details": "studio investment, Airbnb exit",
        },
    },
    {
        "conversation": (
            'CUSTOMER: "Thinking about investing in Cyprus but more in research mode. '
            'Maybe a studio, maybe a 1+1, not decided. '
            'What\'s your price range, do you have a list?"'
        ),
        "output": {
            "thinking": (
                "1. Early discovery phase. "
                "2. No budget, authority or timeline. "
                "3. Price-oriented but single-turn; weak price_fishing signal. "
                "4. Engagement low, not persuadable. "
                "5. Nurture suits; soft close."
            ),
            "challenges_score": 7,
            "challenges_reasoning": "Studio or 1+1 general interest, no concrete",
            "challenges_confidence": 0.4,
            "authority_score": 5,
            "authority_reasoning": "No authority info",
            "authority_confidence": 0.3,
            "money_score": 3,
            "money_reasoning": "No budget shared",
            "money_confidence": 0.2,
            "prioritization_score": 4,
            "prioritization_reasoning": "Research mode, no timing",
            "prioritization_confidence": 0.25,
            "holistic_score": 38,
            "holistic_reasoning": "Potential exists but early phase; leave door open without pressure.",
            "icp_fit_assessment": "Lower-mid fit",
            "negative_signals": ["just_looking"],
            "negative_penalty": -5,
            "negative_reasoning": "Research phase, price-oriented first message",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": None,
                "visit_intent": False,
                "decision_partner_aligned": None,
                "exit_strategy_clear": False,
            },
            "missing_info": ["Budget", "Investment model", "Decision process"],
            "recommended_next_question": "",
            "handoff_ready": False,
            "handoff_reason": "Early phase; pressure-free nurture",
            "cta_recommendation": "nurture",
            "confidence": "low",
            "extracted_budget_range": "",
            "extracted_budget_amount": None,
            "extracted_project_type": "residential_investor",
            "extracted_timeline_urgency": "long",
            "extracted_decision_authority": "",
            "extracted_city": "",
            "extracted_project_details": "general research",
        },
    },
    {
        "conversation": (
            'CUSTOMER: "I\'m in real estate too, benchmarking the market. '
            'Can you share your current price list?"'
        ),
        "output": {
            "thinking": (
                "1. Not a customer, realtor. "
                "2. Market-comparison purpose. "
                "3. Disqualify. No handoff, no CTA."
            ),
            "challenges_score": 0,
            "challenges_reasoning": "No buying need",
            "challenges_confidence": 0.95,
            "authority_score": 0,
            "authority_reasoning": "Industry professional",
            "authority_confidence": 0.95,
            "money_score": 0,
            "money_reasoning": "No buying budget",
            "money_confidence": 0.95,
            "prioritization_score": 0,
            "prioritization_reasoning": "No timing",
            "prioritization_confidence": 0.95,
            "holistic_score": 5,
            "holistic_reasoning": "Competing realtor; disqualify.",
            "icp_fit_assessment": "No fit",
            "negative_signals": ["competitor"],
            "negative_penalty": -100,
            "negative_reasoning": "Explicitly stated being in real estate",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": None,
                "visit_intent": False,
                "decision_partner_aligned": None,
                "exit_strategy_clear": False,
            },
            "missing_info": [],
            "recommended_next_question": "",
            "handoff_ready": False,
            "handoff_reason": "Competing realtor; no handoff",
            "cta_recommendation": "nurture",
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
]


def format_judge_few_shot_examples() -> str:
    """Format examples for prompt injection."""
    parts = ["## Calibration Examples\n"]
    for i, ex in enumerate(JUDGE_EXAMPLES, 1):
        parts.append(f"### Example {i}")
        parts.append(f"Conversation: {ex['conversation']}")
        parts.append(
            f"Expected Output:\n```json\n{json.dumps(ex['output'], ensure_ascii=False, indent=2)}\n```\n"
        )
    return "\n".join(parts)
