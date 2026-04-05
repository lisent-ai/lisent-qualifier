"""
Turkish language sentiment, intent, and buying signal analysis.

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
    "harika",
    "mukemmel",
    "mükemmel",
    "cok guzel",
    "çok güzel",
    "ilginc",
    "ilginç",
    "hemen",
    "tamam",
    "anladim",
    "anladım",
    "olur",
    "evet",
    "kesinlikle",
    "super",
    "süper",
    "tesekkurler",
    "teşekkürler",
    "sagolun",
    "sağolun",
    "memnun",
    "guzel",
    "güzel",
]

NEGATIVE_KEYWORDS: list[str] = [
    "pahali",
    "pahalı",
    "dusunecegim",
    "düşüneceğim",
    "emin degilim",
    "emin değilim",
    "sonra bakariz",
    "sonra bakarız",
    "simdilik yok",
    "şimdilik yok",
    "gerek yok",
    "istemiyorum",
    "istemiyoruz",
    "vazgectim",
    "vazgeçtim",
    "cok pahali",
    "çok pahalı",
    "butcemiz yok",
    "bütçemiz yok",
    "param yok",
]

# ── Intent lexicons ───────────────────────────────────────────────────────────

HIGH_INTENT_KEYWORDS: list[str] = [
    "sozlesme",
    "sözleşme",
    "kontrat",
    "vade",
    "goruselim",
    "görüşelim",
    "toplanti",
    "toplantı",
    "randevu",
    "ne zaman baslayabiliriz",
    "ne zaman başlayabiliriz",
    "ne zaman baslarsiniz",
    "ne zaman başlarsınız",
    "hemen baslayalim",
    "hemen başlayalım",
    "teklif",
    "odeme",
    "ödeme",
    "depozito",
    "kapora",
    "ofise gelebilir miyim",
]

LOW_INTENT_KEYWORDS: list[str] = [
    "sadece bakiyorum",
    "sadece bakıyorum",
    "merak ettim",
    "fiyat nedir",
    "fiyat ne kadar",
    "bilgi almak istiyorum",
    "arastiriyorum",
    "araştırıyorum",
    "ilerde belki",
    "ileride belki",
    "su an degil",
    "şu an değil",
    "henuz karar vermedim",
    "henüz karar vermedim",
]

OFF_TOPIC_KEYWORDS: list[str] = [
    "futbol",
    "mac skoru",
    "maç skoru",
    "siyaset",
    "secim",
    "seçim",
    "hava durumu",
    "film",
    "dizi",
    "muzik",
    "müzik",
]

# ── Buying signals ────────────────────────────────────────────────────────────

BUYING_SIGNAL_PATTERNS: dict[str, str] = {
    "sozlesme": "sözleşme/kontrat sordu",
    "sözleşme": "sözleşme/kontrat sordu",
    "kontrat": "sözleşme/kontrat sordu",
    "vade": "ödeme vadesi sordu",
    "taksit": "taksit imkanı sordu",
    "odeme": "ödeme koşullarını sordu",
    "ödeme": "ödeme koşullarını sordu",
    "goruselim": "yüz yüze görüşme talep etti",
    "görüşelim": "yüz yüze görüşme talep etti",
    "toplanti": "toplantı talep etti",
    "toplantı": "toplantı talep etti",
    "randevu": "randevu talep etti",
    "referans": "referans/portföy istedi",
    "daha once": "önceki projeler hakkında sordu",
    "ornekler": "örnek projeler istedi",
    "ne zaman baslayabilirsiniz": "başlangıç tarihi sordu",
    "ne zaman başlayabilirsiniz": "başlangıç tarihi sordu",
    "teslim": "teslim tarihi sordu",
    "garanti": "garanti koşullarını sordu",
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
