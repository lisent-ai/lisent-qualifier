"""
English real-estate (Cyprus/KKTC investor) few-shot examples for CHAMP extraction.

Calibrated for residential investor leads: villa/apartment/studio shoppers
evaluating Cyprus property for rental yield, flip, or holiday-let use.
sector_qualifiers use the real-estate schema.
"""

EXAMPLES: list[dict] = [
    {
        "conversation": (
            'CUSTOMER: "Looking at a sea-view villa on the Kyrenia side. '
            'I\'ve shortlisted two projects in Esentepe and Catalkoy. '
            'It\'s an investment — most likely I\'ll run it on Airbnb. '
            'Budget is 600-800k EUR, cash ready. '
            'I\'m the decision maker. If the right one comes up I\'d like to '
            'come over and see it in the next 1-2 months."'
        ),
        "output": {
            "challenges_score": 23,
            "authority_score": 24,
            "money_score": 24,
            "prioritization_score": 22,
            "challenges_notes": "Sea-view villa in Kyrenia, Esentepe/Catalkoy shortlisted, Airbnb exit",
            "authority_notes": "Sole decision maker",
            "money_notes": "600-800k EUR cash ready",
            "prioritization_notes": "Cyprus visit intended in next 1-2 months",
            "challenges_confidence": 0.95,
            "authority_confidence": 0.95,
            "money_confidence": 0.95,
            "prioritization_confidence": 0.85,
            "confidence": "high",
            "sector_qualifiers": {
                "has_property_shortlist": 2,
                "financing_ready": "cash",
                "visit_intent": True,
                "decision_partner_aligned": True,
                "exit_strategy_clear": True,
                "property_type": "villa",
                "location": "esentepe/catalkoy",
            },
        },
    },
    {
        "conversation": (
            'CUSTOMER: "Thinking about investing in Cyprus but I haven\'t decided. '
            'Just gathering general info — which areas, what prices look like."'
        ),
        "output": {
            "challenges_score": 8,
            "authority_score": 8,
            "money_score": 2,
            "prioritization_score": 3,
            "challenges_notes": "General investment interest, no concrete area or property type",
            "authority_notes": "Speaking for self but authority unclear",
            "money_notes": "No budget mentioned",
            "prioritization_notes": "Research stage, no visit intent",
            "challenges_confidence": 0.45,
            "authority_confidence": 0.35,
            "money_confidence": 0.1,
            "prioritization_confidence": 0.15,
            "confidence": "low",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": None,
                "visit_intent": False,
                "decision_partner_aligned": None,
                "exit_strategy_clear": False,
            },
        },
    },
    {
        "conversation": (
            'CUSTOMER: "I\'m in Turkey, would like to get info remotely first. '
            'Could look at a 2+1 or a small villa in Kyrenia with sea view. '
            'Budget not finalised but I\'d consider a mortgage. '
            'We\'ll review it together with my wife."'
        ),
        "output": {
            "challenges_score": 17,
            "authority_score": 10,
            "money_score": 8,
            "prioritization_score": 7,
            "challenges_notes": "Kyrenia, sea view, 2+1 or small villa",
            "authority_notes": "Joint review with spouse; authority structure unclear",
            "money_notes": "Budget not finalised, open to mortgage",
            "prioritization_notes": "Remote info stage, no visit intent",
            "challenges_confidence": 0.8,
            "authority_confidence": 0.4,
            "money_confidence": 0.35,
            "prioritization_confidence": 0.3,
            "confidence": "medium",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": "pending",
                "visit_intent": False,
                "decision_partner_aligned": False,
                "exit_strategy_clear": False,
                "property_type": "apartment",
                "location": "kyrenia",
            },
        },
    },
    {
        "conversation": (
            'CUSTOMER: "Looking for a seafront villa for Airbnb. '
            '50k GBP deposit ready, can top up with a mortgage. '
            'My wife and I decide together but I carry the final call. '
            'We\'re in Cyprus during the summer anyway so we\'d visit then."'
        ),
        "output": {
            "challenges_score": 21,
            "authority_score": 17,
            "money_score": 16,
            "prioritization_score": 14,
            "challenges_notes": "Seafront villa for Airbnb, exit strategy clear",
            "authority_notes": "Joint with spouse but final call with customer",
            "money_notes": "50k GBP deposit + mortgage top-up",
            "prioritization_notes": "Summer visit intent, specific date not set",
            "challenges_confidence": 0.9,
            "authority_confidence": 0.7,
            "money_confidence": 0.75,
            "prioritization_confidence": 0.5,
            "confidence": "medium",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": "pending",
                "visit_intent": True,
                "decision_partner_aligned": True,
                "exit_strategy_clear": True,
                "property_type": "villa",
            },
        },
    },
    {
        "conversation": (
            'CUSTOMER: "First time investing in Northern Cyprus. '
            'Looking at a 1+1 or 2+1 close to the sea. '
            'Budget 220-280k GBP. 2-3 year instalment + deposit structure matters to me. '
            'If I find the right project I\'d like to move before summer and see it in person."'
        ),
        "output": {
            "challenges_score": 19,
            "authority_score": 12,
            "money_score": 18,
            "prioritization_score": 19,
            "challenges_notes": "First NC investment, 1+1 or 2+1 near sea",
            "authority_notes": "Speaking for self but structure unclear",
            "money_notes": "220-280k GBP, active interest in instalments/deposit",
            "prioritization_notes": "Wants to move before summer + visit intent explicit",
            "challenges_confidence": 0.85,
            "authority_confidence": 0.45,
            "money_confidence": 0.85,
            "prioritization_confidence": 0.85,
            "confidence": "high",
            "sector_qualifiers": {
                "has_property_shortlist": 0,
                "financing_ready": "pending",
                "visit_intent": True,
                "decision_partner_aligned": False,
                "exit_strategy_clear": False,
                "property_type": "apartment",
            },
        },
    },
]


def format_few_shot_examples() -> str:
    """Format examples as a string block for injection into extraction prompt."""
    import json

    parts: list[str] = ["## Examples\n"]
    for i, ex in enumerate(EXAMPLES, 1):
        output_str = json.dumps(ex["output"], ensure_ascii=False, indent=2)
        parts.append(f"### Example {i}\n{ex['conversation']}\nResult:\n{output_str}\n")
    return "\n".join(parts)
