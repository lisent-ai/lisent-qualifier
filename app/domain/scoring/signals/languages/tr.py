"""
Turkish language sentiment, intent, and buying signal analysis.

All analysis is keyword-based with zero LLM cost.
"""
from __future__ import annotations

from app.domain.scoring.signals.base import (
    BuyingSignalDetector,
    IntentResult,
    SentimentAnalyzer,
    SentimentResult,
)

POSITIVE_KEYWORDS: list[str] = [
    "harika",
    "mukemmel",
    "cok guzel",
    "ilginc",
    "hemen",
    "tamam",
    "anladim",
    "olur",
    "evet",
    "kesinlikle",
    "super",
    "tesekkurler",
    "sagolun",
    "memnun",
    "guzel",
]

NEGATIVE_KEYWORDS: list[str] = [
    "pahali",
    "emin degilim",
    "simdilik yok",
    "gerek yok",
    "istemiyorum",
    "istemiyoruz",
    "vazgectim",
    "cok pahali",
    "butcemiz yok",
    "param yok",
]

HIGH_INTENT_KEYWORDS: list[str] = [
    "sozlesme",
    "kontrat",
    "vade",
    "goruselim",
    "toplanti",
    "randevu",
    "ne zaman baslayabiliriz",
    "ne zaman baslarsiniz",
    "hemen baslayalim",
    "teklif",
    "odeme",
    "depozito",
    "kapora",
    "ofise gelebilir miyim",
]

LOW_INTENT_KEYWORDS: list[str] = [
    "sadece bakiyorum",
    "merak ettim",
    "fiyat nedir",
    "fiyat ne kadar",
    "bilgi almak istiyorum",
    "arastiriyorum",
    "ilerde belki",
    "su an degil",
    "henuz karar vermedim",
]

OFF_TOPIC_KEYWORDS: list[str] = [
    "futbol",
    "mac skoru",
    "siyaset",
    "secim",
    "hava durumu",
    "film",
    "dizi",
    "muzik",
]

BUYING_SIGNAL_PATTERNS: dict[str, str] = {
    "sozlesme": "sozlesme/kontrat sordu",
    "kontrat": "sozlesme/kontrat sordu",
    "vade": "odeme vadesi sordu",
    "taksit": "taksit imkani sordu",
    "odeme": "odeme kosullarini sordu",
    "goruselim": "yuz yuze gorusme talep etti",
    "toplanti": "toplanti talep etti",
    "randevu": "randevu talep etti",
    "referans": "referans/portfoy istedi",
    "daha once": "onceki projeler hakkinda sordu",
    "ornekler": "ornek projeler istedi",
    "ne zaman baslayabilirsiniz": "baslangic tarihi sordu",
    "teslim": "teslim tarihi sordu",
    "garanti": "garanti kosullarini sordu",
    "kapora": "kapora/depozito sordu",
    "depozito": "kapora/depozito sordu",
}


def _text_lower(text: str) -> str:
    return text.lower().replace("i̇", "i")


class TurkishSentimentAnalyzer(SentimentAnalyzer):
    def analyze(self, text: str) -> SentimentResult:
        lower = _text_lower(text)

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
        lower = _text_lower(text)

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


class TurkishBuyingSignalDetector(BuyingSignalDetector):
    def detect(self, text: str) -> list[str]:
        lower = _text_lower(text)
        seen: set[str] = set()
        signals: list[str] = []
        for keyword, description in BUYING_SIGNAL_PATTERNS.items():
            if keyword in lower and description not in seen:
                signals.append(description)
                seen.add(description)
        return signals
