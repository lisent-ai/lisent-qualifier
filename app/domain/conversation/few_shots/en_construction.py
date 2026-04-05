"""
English construction sector few-shot examples for CHAMP extraction.
"""

EXAMPLES: list[dict] = [
    {
        "conversation": (
            'CUSTOMER: "We have a $2M budget for a commercial building in downtown. '
            'I\'m the sole decision maker and we need to start within 2 months."'
        ),
        "output": {
            "challenges_score": 20,
            "authority_score": 23,
            "money_score": 22,
            "prioritization_score": 20,
            "challenges_notes": "Commercial building, downtown, clear project type",
            "authority_notes": "Sole decision maker, explicitly stated",
            "money_notes": "$2M budget stated",
            "prioritization_notes": "Within 2 months",
            "challenges_confidence": 0.8,
            "authority_confidence": 0.9,
            "money_confidence": 0.85,
            "prioritization_confidence": 0.85,
            "confidence": "high",
        },
    },
    {
        "conversation": 'CUSTOMER: "How much does construction cost per square foot?"',
        "output": {
            "challenges_score": 5,
            "authority_score": 0,
            "money_score": 3,
            "prioritization_score": 0,
            "challenges_notes": "General construction interest, no specific project",
            "authority_notes": "No information",
            "money_notes": "Asking about price but no budget disclosed",
            "prioritization_notes": "No timeline information",
            "challenges_confidence": 0.2,
            "authority_confidence": 0.0,
            "money_confidence": 0.1,
            "prioritization_confidence": 0.0,
            "confidence": "low",
        },
    },
    {
        "conversation": (
            'CUSTOMER: "I own the land, zoning is approved. Looking to build a villa '
            'but haven\'t finalized the budget yet."'
        ),
        "output": {
            "challenges_score": 18,
            "authority_score": 10,
            "money_score": 5,
            "prioritization_score": 8,
            "challenges_notes": "Villa project, land owned, zoning approved",
            "authority_notes": "Looking for themselves but decision authority unclear",
            "money_notes": "Budget not finalized",
            "prioritization_notes": "No urgency stated but land is ready",
            "challenges_confidence": 0.85,
            "authority_confidence": 0.4,
            "money_confidence": 0.2,
            "prioritization_confidence": 0.3,
            "confidence": "medium",
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
