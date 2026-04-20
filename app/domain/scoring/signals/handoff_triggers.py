"""Layer 1: Instant programmatic handoff triggers.

Runs on every inbound message with zero LLM cost.
Returns simple booleans for immediate routing decisions.
"""

INSTANT_HANDOFF_TR: list[str] = [
    "insan istiyorum",
    "gercek kisi",
    "biriyle gorusmek",
    "yetkili ile",
    "mudur ile",
    "temsilci ile",
    "goruselim",
    "toplanti",
    "randevu",
    "yuz yuze",
    "ofise geleyim",
    "ziyaret",
    "sozlesme",
    "kontrat",
    "teklif gonderin",
    "ne zaman baslayabiliriz",
    "hemen baslamak",
]

INSTANT_HANDOFF_EN: list[str] = [
    "talk to someone",
    "speak to a person",
    "real person",
    "human agent",
    "let's meet",
    "schedule a meeting",
    "set up a call",
    "send me a quote",
    "contract",
    "proposal",
    "when can we start",
    "let's get started",
]

VIP_BYPASS_TR: list[str] = [
    "ihale",
    "rfp",
    "teklif dosyasi",
    "resmi talep",
]

VIP_BYPASS_EN: list[str] = [
    "tender",
    "rfp",
    "bid request",
    "formal request",
]


CONVERSATION_END_TR: list[str] = [
    "gorusuruz",
    "hosca kalin",
    "iyi gunler",
    "iyi aksamlar",
    "tamam tesekkurler",
    "tamamdir",
    "tamam sagol",
    "yeterli tesekkurler",
]

CONVERSATION_END_EN: list[str] = [
    "goodbye",
    "bye",
    "thanks bye",
    "thank you bye",
    "have a good day",
    "that's all",
    "talk to you later",
    "thanks for the info",
]

SOFT_PAUSE_TR: list[str] = [
    "ben dusuneyim",
    "bir dusuneyim",
    "sonra bakariz",
    "daha sonra donerim",
    "evleri goreyim",
]

SOFT_PAUSE_EN: list[str] = [
    "i'll think about it",
    "i will think about it",
    "let me think about it",
    "i'll get back to you",
    "i will get back to you",
    "let me review it",
]


def check_instant_handoff(message: str, language: str = "tr") -> tuple[bool, str]:
    """Check message for instant handoff triggers."""
    msg_lower = message.lower()

    triggers = INSTANT_HANDOFF_TR if language == "tr" else INSTANT_HANDOFF_EN
    for trigger in triggers:
        if trigger in msg_lower:
            return True, f"instant_trigger:{trigger}"

    vip = VIP_BYPASS_TR if language == "tr" else VIP_BYPASS_EN
    for value in vip:
        if value in msg_lower:
            return True, f"vip_bypass:{value}"

    urgent_phrases = (
        ("acil", "yetkili", "temsilci", "gorus", "ara", "arayin")
        if language == "tr"
        else ("urgent", "agent", "person", "call", "speak", "contact")
    )
    if urgent_phrases[0] in msg_lower and any(token in msg_lower for token in urgent_phrases[1:]):
        return True, f"instant_trigger:{urgent_phrases[0]}"

    return False, ""


def check_conversation_end(message: str, language: str = "tr") -> tuple[bool, str]:
    """Check if user is explicitly ending the conversation.

    Soft deferment such as "I'll think about it" should not force a handoff.
    Those cases stay in nurture mode and are handled by normal conversation flow.
    """
    msg_lower = message.lower().strip()

    soft_pauses = SOFT_PAUSE_TR if language == "tr" else SOFT_PAUSE_EN
    for phrase in soft_pauses:
        if phrase in msg_lower:
            return False, ""

    endings = CONVERSATION_END_TR if language == "tr" else CONVERSATION_END_EN
    for ending in endings:
        if ending in msg_lower:
            return True, f"conversation_end:{ending}"

    return False, ""
