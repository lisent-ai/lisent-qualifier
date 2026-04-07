"""
English construction sector few-shot anchors for qualification judge.

5 calibration examples spanning the full score range (15, 35, 55, 75, 92).
"""
import json

JUDGE_EXAMPLES: list[dict] = [
    # ── Score ~92: Highly qualified lead ──────────────────────────────────
    {
        "conversation": (
            'CUSTOMER: "We own a 5000 sqft lot in downtown Miami, zoned commercial. '
            "Planning a 20-room boutique hotel. Already working with an architect on "
            "preliminary designs. Budget is $3.5M cash. I'm the sole owner and decision "
            "maker. Want to break ground within 2 months. We've gotten quotes from two "
            'other builders but your portfolio impressed us."'
        ),
        "output": {
            "thinking": (
                "1. Very specific project: 20-room boutique hotel, downtown Miami. "
                "2. Sole owner and decision maker — strongest authority signal. "
                "3. $3.5M cash budget, well-scoped for the project. "
                "4. 2-month timeline — very urgent. "
                "5. ICP: land owned, zoned commercial, architect engaged, cash — perfect match. "
                "6. No red flags. Competing bids = serious buyer."
            ),
            "challenges_score": 24,
            "challenges_reasoning": "20-room boutique hotel, downtown Miami, 5000 sqft lot — highly specific",
            "challenges_confidence": 0.95,
            "authority_score": 25,
            "authority_reasoning": "'I'm the sole owner and decision maker' — definitive",
            "authority_confidence": 1.0,
            "money_score": 24,
            "money_reasoning": "$3.5M cash budget stated explicitly, scope-appropriate",
            "money_confidence": 0.95,
            "prioritization_score": 23,
            "prioritization_reasoning": "'Break ground within 2 months' — very urgent",
            "prioritization_confidence": 0.9,
            "holistic_score": 92,
            "holistic_reasoning": "Near-perfect ICP match. All dimensions strong. Land, zoning, architect, budget, timeline ready. Competing bids show seriousness.",
            "icp_fit_assessment": "Perfect match — 500sqm+ commercial, $3.5M budget, decision maker, 2-month timeline, land and permits ready",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": True,
                "permit_status": "approved",
                "has_architect": True,
                "budget_source": "cash",
                "competing_bids": True,
                "project_sqm": 465,
            },
            "missing_info": ["Specific design requirements"],
            "recommended_next_question": "What specific features or design elements are most important for the hotel?",
            "handoff_ready": True,
            "handoff_reason": "All CHAMP dimensions strong, perfect ICP match, urgent timeline",
            "confidence": "high",
        },
    },
    # ── Score ~75: Good lead with gaps ────────────────────────────────────
    {
        "conversation": (
            'CUSTOMER: "My wife and I want to build a custom home in Austin. '
            "We're thinking 3500-4000 sqft, modern design. Budget is roughly $800K-1M. "
            "We're still looking for the right lot but hoping to start within 6 months.\""
        ),
        "output": {
            "thinking": (
                "1. Custom home, Austin — clear project type but details sparse. "
                "2. Joint decision with wife — not sole authority. "
                "3. $800K-1M range — reasonable but not firm. "
                "4. 6 months — moderate urgency. "
                "5. ICP: no land yet — significant gap. Budget in range."
            ),
            "challenges_score": 16,
            "challenges_reasoning": "Custom home, 3500-4000 sqft, modern — good detail but specifics TBD",
            "challenges_confidence": 0.7,
            "authority_score": 15,
            "authority_reasoning": "'My wife and I' — joint decision",
            "authority_confidence": 0.7,
            "money_score": 20,
            "money_reasoning": "$800K-1M range stated, reasonable for scope",
            "money_confidence": 0.8,
            "prioritization_score": 15,
            "prioritization_reasoning": "'Within 6 months' — moderate timeline",
            "prioritization_confidence": 0.7,
            "holistic_score": 75,
            "holistic_reasoning": "Serious buyer. Budget clear, timeline reasonable. Main gap: no land yet. Joint decision adds minor risk.",
            "icp_fit_assessment": "Partial match — budget and timeline fit, but no land and joint decision",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "",
            "sector_qualifiers": {
                "has_land": False,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": 370,
            },
            "missing_info": ["Land status details", "Architect engagement", "Budget source"],
            "recommended_next_question": "Have you started working with an architect on the design?",
            "handoff_ready": False,
            "handoff_reason": "Land status and project details still missing, conversation progressing",
            "confidence": "medium",
        },
    },
    # ── Score ~55: Borderline ─────────────────────────────────────────────
    {
        "conversation": (
            'CUSTOMER: "I\'m thinking about investing in construction but don\'t '
            "have a specific plan yet. Need to figure out our budget. Maybe in a year.\""
        ),
        "output": {
            "thinking": (
                "1. General interest, no specific project. "
                "2. Likely a decision maker ('investing'). "
                "3. Budget not determined. "
                "4. 'Maybe in a year' — long, uncertain timeline."
            ),
            "challenges_score": 8,
            "challenges_reasoning": "'Investing in construction' — general interest, no project",
            "challenges_confidence": 0.4,
            "authority_score": 12,
            "authority_reasoning": "Speaks as investor — probably decision maker",
            "authority_confidence": 0.5,
            "money_score": 5,
            "money_reasoning": "'Need to figure out budget' — not yet determined",
            "money_confidence": 0.3,
            "prioritization_score": 8,
            "prioritization_reasoning": "'Maybe in a year' — vague, long-term",
            "prioritization_confidence": 0.4,
            "holistic_score": 55,
            "holistic_reasoning": "Potential but early stage. No project, budget, or firm timeline. Worth nurturing, not handoff.",
            "icp_fit_assessment": "Low match — no ICP criteria met yet",
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
            "missing_info": ["Project type", "Location", "Budget range", "Timeline"],
            "recommended_next_question": "What type of project interests you — residential or commercial?",
            "handoff_ready": False,
            "handoff_reason": "Early stage, project and budget unclear",
            "confidence": "low",
        },
    },
    # ── Score ~35: Low quality ────────────────────────────────────────────
    {
        "conversation": (
            'CUSTOMER: "How much does construction cost per square foot?"'
        ),
        "output": {
            "thinking": (
                "1. Just asking about costs — no specific project. "
                "2. No authority info. "
                "3. Asking price without sharing budget — early price fishing signal. "
                "4. No timeline."
            ),
            "challenges_score": 5,
            "challenges_reasoning": "General cost inquiry, no project details",
            "challenges_confidence": 0.3,
            "authority_score": 0,
            "authority_reasoning": "No information",
            "authority_confidence": 0.1,
            "money_score": 3,
            "money_reasoning": "Asking about costs but no own budget shared",
            "money_confidence": 0.2,
            "prioritization_score": 0,
            "prioritization_reasoning": "No timeline information",
            "prioritization_confidence": 0.1,
            "holistic_score": 35,
            "holistic_reasoning": "General inquiry. No project, budget, authority, or timeline. Needs more info.",
            "icp_fit_assessment": "Very low — no ICP criteria met",
            "negative_signals": [],
            "negative_penalty": 0,
            "negative_reasoning": "Single message, too early to determine",
            "sector_qualifiers": {
                "has_land": None,
                "permit_status": None,
                "has_architect": None,
                "budget_source": None,
                "competing_bids": None,
                "project_sqm": None,
            },
            "missing_info": ["Project type", "Location", "Budget", "Decision maker", "Timeline"],
            "recommended_next_question": "What type of construction project are you considering?",
            "handoff_ready": False,
            "handoff_reason": "Insufficient information, conversation should continue",
            "confidence": "low",
        },
    },
    # ── Score ~15: Disqualified / competitor ──────────────────────────────
    {
        "conversation": (
            'CUSTOMER: "We\'re a construction company ourselves, just checking your '
            'pricing for a market analysis we\'re doing."'
        ),
        "output": {
            "thinking": (
                "1. Explicitly stated they are a construction company. "
                "2. 'Market analysis' — competitor intelligence, not a customer. "
                "3. Disqualify immediately."
            ),
            "challenges_score": 0,
            "challenges_reasoning": "Competitor — no real project",
            "challenges_confidence": 0.9,
            "authority_score": 0,
            "authority_reasoning": "Not a customer, competitor employee",
            "authority_confidence": 0.9,
            "money_score": 0,
            "money_reasoning": "No purchase budget — competitor research",
            "money_confidence": 0.9,
            "prioritization_score": 0,
            "prioritization_reasoning": "No project, no buying intent",
            "prioritization_confidence": 0.9,
            "holistic_score": 5,
            "holistic_reasoning": "Competitor — disqualify. Do not waste sales time.",
            "icp_fit_assessment": "Not a match — competitor, not a potential customer",
            "negative_signals": ["competitor"],
            "negative_penalty": -100,
            "negative_reasoning": "'We're a construction company ourselves' — explicit competitor",
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
            "handoff_reason": "Competitor, handoff not applicable",
            "confidence": "high",
        },
    },
]


def format_judge_few_shot_examples() -> str:
    """Format examples for prompt injection."""
    parts = ["## Calibration Examples\n"]
    for i, ex in enumerate(JUDGE_EXAMPLES, 1):
        parts.append(f"### Example {i}")
        parts.append(f"Conversation: {ex['conversation']}")
        parts.append(f"Expected Output:\n```json\n{json.dumps(ex['output'], ensure_ascii=False, indent=2)}\n```\n")
    return "\n".join(parts)
