"""
English language sentiment, intent, and buying signal analysis.

All analysis is keyword-based — zero LLM calls, zero latency.
"""
from __future__ import annotations

from app.domain.scoring.signals.base import (
    BuyingSignalDetector,
    IntentResult,
    SentimentAnalyzer,
    SentimentResult,
)

# ── Sentiment lexicons ────────────────────────────────────────────────────────

POSITIVE_KEYWORDS: list[str] = [
    "great",
    "excellent",
    "perfect",
    "wonderful",
    "amazing",
    "sounds good",
    "yes",
    "absolutely",
    "definitely",
    "love it",
    "interested",
    "excited",
    "let's do it",
    "thank you",
    "thanks",
    "impressive",
    "exactly",
]

NEGATIVE_KEYWORDS: list[str] = [
    "expensive",
    "too much",
    "not sure",
    "maybe later",
    "i'll think about it",
    "not interested",
    "no budget",
    "can't afford",
    "no need",
    "don't need",
    "not right now",
    "pass",
    "not for us",
    "too costly",
    "over budget",
]

# ── Intent lexicons ───────────────────────────────────────────────────────────

HIGH_INTENT_KEYWORDS: list[str] = [
    "contract",
    "agreement",
    "let's meet",
    "schedule a meeting",
    "when can you start",
    "proposal",
    "quote",
    "payment terms",
    "deposit",
    "down payment",
    "timeline for delivery",
    "sign",
    "close the deal",
    "move forward",
    "ready to start",
]

LOW_INTENT_KEYWORDS: list[str] = [
    "just looking",
    "just browsing",
    "curious about",
    "what's the price",
    "how much",
    "exploring options",
    "maybe in the future",
    "not decided yet",
    "still researching",
    "gathering information",
]

OFF_TOPIC_KEYWORDS: list[str] = [
    "football",
    "soccer",
    "politics",
    "election",
    "weather",
    "movie",
    "music",
    "tv show",
]

# ── Buying signals ────────────────────────────────────────────────────────────

BUYING_SIGNAL_PATTERNS: dict[str, str] = {
    "contract": "asked about contract/agreement",
    "agreement": "asked about contract/agreement",
    "payment terms": "asked about payment terms",
    "installment": "asked about installment options",
    "deposit": "asked about deposit/down payment",
    "down payment": "asked about deposit/down payment",
    "let's meet": "requested in-person meeting",
    "schedule a meeting": "requested meeting",
    "when can you start": "asked about start date",
    "delivery date": "asked about delivery timeline",
    "references": "asked for references/portfolio",
    "portfolio": "asked for references/portfolio",
    "previous projects": "asked about past work",
    "warranty": "asked about warranty terms",
    "guarantee": "asked about guarantee terms",
}


class EnglishSentimentAnalyzer(SentimentAnalyzer):
    def analyze(self, text: str) -> SentimentResult:
        lower = text.lower()

        pos_matches = [kw for kw in POSITIVE_KEYWORDS if kw in lower]
        neg_matches = [kw for kw in NEGATIVE_KEYWORDS if kw in lower]

        pos_count = len(pos_matches)
        neg_count = len(neg_matches)

        if pos_count > neg_count:
            score = min(1.0, 0.3 + pos_count * 0.2)
            return SentimentResult(score=score, label="positive", matched_keywords=pos_matches)
        if neg_count > pos_count:
            score = max(-1.0, -0.3 - neg_count * 0.2)
            return SentimentResult(score=score, label="negative", matched_keywords=neg_matches)

        return SentimentResult(score=0.0, label="neutral")

    def detect_intent(self, text: str) -> IntentResult:
        lower = text.lower()

        high = [kw for kw in HIGH_INTENT_KEYWORDS if kw in lower]
        if high:
            return IntentResult(intent="high_intent", matched_keywords=high)

        off = [kw for kw in OFF_TOPIC_KEYWORDS if kw in lower]
        if off:
            return IntentResult(intent="off_topic", matched_keywords=off)

        low = [kw for kw in LOW_INTENT_KEYWORDS if kw in lower]
        if low:
            return IntentResult(intent="low_intent", matched_keywords=low)

        return IntentResult(intent="neutral")


class EnglishBuyingSignalDetector(BuyingSignalDetector):
    def detect(self, text: str) -> list[str]:
        lower = text.lower()
        seen: set[str] = set()
        signals: list[str] = []
        for keyword, description in BUYING_SIGNAL_PATTERNS.items():
            if keyword in lower and description not in seen:
                signals.append(description)
                seen.add(description)
        return signals
