"""Layer 1: Instant programmatic handoff triggers.

Runs on EVERY inbound message, <1ms latency, zero LLM cost.
Returns (should_handoff, reason) tuple for immediate routing decisions.
"""

INSTANT_HANDOFF_TR: list[str] = [
    "insan istiyorum",
    "gercek kisi",
    "gerçek kişi",
    "biriyle gorusmek",
    "biriyle görüşmek",
    "yetkili ile",
    "mudur ile",
    "müdür ile",
    "temsilci ile",
    "goruselim",
    "görüşelim",
    "toplanti",
    "toplantı",
    "randevu",
    "yuz yuze",
    "yüz yüze",
    "ofise geleyim",
    "ziyaret",
    "sozlesme",
    "sözleşme",
    "kontrat",
    "teklif gonderin",
    "teklif gönderin",
    "ne zaman baslayabiliriz",
    "ne zaman başlayabiliriz",
    "hemen baslamak",
    "hemen başlamak",
    "acil",
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
    "urgent",
]

VIP_BYPASS_TR: list[str] = [
    "ihale",
    "rfp",
    "teklif dosyasi",
    "teklif dosyası",
    "resmi talep",
]

VIP_BYPASS_EN: list[str] = [
    "tender",
    "rfp",
    "bid request",
    "formal request",
]


def check_instant_handoff(
    message: str, language: str = "tr"
) -> tuple[bool, str]:
    """Check message for instant handoff triggers.

    Returns (should_handoff, reason). Runs on EVERY message, <1ms.
    """
    msg_lower = message.lower()

    triggers = INSTANT_HANDOFF_TR if language == "tr" else INSTANT_HANDOFF_EN
    for trigger in triggers:
        if trigger in msg_lower:
            return True, f"instant_trigger:{trigger}"

    vip = VIP_BYPASS_TR if language == "tr" else VIP_BYPASS_EN
    for v in vip:
        if v in msg_lower:
            return True, f"vip_bypass:{v}"

    return False, ""
