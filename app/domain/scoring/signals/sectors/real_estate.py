"""
Real-estate sector (Cyprus/KKTC investor focus) — negative signals and seasonality.

Targets residential real-estate investor leads. Price fishing thresholds are
stricter than construction because investors typically disclose budget early;
persistent price-only queries without commitment are a stronger negative signal.
Seasonality mirrors Mediterranean tourism rhythm: summer-driven decisions.
"""
from __future__ import annotations

from datetime import datetime

from app.domain.scoring.signals.base import NegativeSignalDetector, NegativeSignalResult

_PRICE_FISHING_PATTERNS: list[str] = [
    "fiyat nedir",
    "fiyat ne kadar",
    "ne kadar tutar",
    "metrekare fiyati",
    "metrekare fiyatı",
    "m2 fiyati",
    "m2 fiyatı",
    "pesinat iskonto",
    "peşinat iskonto",
    "indirim var mi",
    "indirim var mı",
    "what's the price",
    "how much does it cost",
    "price per sqm",
    "any discount",
]

_JUST_LOOKING_PATTERNS: list[str] = [
    "sadece bakiyorum",
    "sadece bakıyorum",
    "merak ettim",
    "arastiriyorum",
    "araştırıyorum",
    "fikir alıyorum",
    "fikir aliyorum",
    "ileride bakarım",
    "ileride bakarim",
    "just looking",
    "just browsing",
    "just curious",
    "exploring options",
    "not ready yet",
]

_NOT_INVESTMENT_PATTERNS: list[str] = [
    "ilk evim olacak",
    "ilk ev",
    "oturmak icin",
    "oturmak için",
    "aileme yerlesmek",
    "aileme yerleşmek",
    "kendim oturacagim",
    "kendim oturacağım",
    "primary residence",
    "not for investment",
]

_COMPETITOR_PATTERNS: list[str] = [
    "ben de emlakciyim",
    "ben de emlakçıyım",
    "emlak ofisim var",
    "rakip emlak",
    "biz de gayrimenkul",
    "i'm a realtor",
    "i work in real estate",
    "i'm a broker",
]


class RealEstateNegativeDetector(NegativeSignalDetector):
    def detect(
        self,
        messages: list[dict],
        hours_since_last_activity: float = 0.0,
    ) -> NegativeSignalResult:
        total_penalty = 0
        signals: list[str] = []

        user_msgs = [m for m in messages if m.get("role") == "user"]

        price_asks = 0
        has_budget_info = False
        for m in user_msgs:
            text = m.get("content", "").lower()
            if any(p in text for p in _PRICE_FISHING_PATTERNS):
                price_asks += 1
            if any(
                kw in text
                for kw in [
                    "butce",
                    "bütçe",
                    "budget",
                    "sterlin",
                    "eur",
                    "euro",
                    "gbp",
                    "usd",
                    "dolar",
                    "pound",
                    "bin",
                    "milyon",
                    "million",
                    "k gbp",
                    "k eur",
                ]
            ):
                has_budget_info = True

        if price_asks >= 2 and not has_budget_info:
            penalty = min(price_asks * -10, -30)
            total_penalty += penalty
            signals.append(f"Fiyat soruyor ama bütçe vermiyor ({price_asks}x)")

        for m in user_msgs:
            text = m.get("content", "").lower()
            if any(p in text for p in _JUST_LOOKING_PATTERNS):
                total_penalty += -15
                signals.append("Sadece araştırma/bakınma aşamasında")
                break

        for m in user_msgs:
            text = m.get("content", "").lower()
            if any(p in text for p in _NOT_INVESTMENT_PATTERNS):
                total_penalty += -8
                signals.append("Yatırım değil birincil konut arayışı")
                break

        for m in user_msgs:
            text = m.get("content", "").lower()
            if any(p in text for p in _COMPETITOR_PATTERNS):
                total_penalty = -100
                signals = ["Rakip emlakçı tespit edildi — diskalifiye"]
                return NegativeSignalResult(total_penalty=total_penalty, signals=signals)

        if hours_since_last_activity > 2:
            if hours_since_last_activity > 48:
                decay = -25
            elif hours_since_last_activity > 24:
                decay = -15
            elif hours_since_last_activity > 6:
                decay = -10
            else:
                decay = -5
            total_penalty += decay
            signals.append(f"{hours_since_last_activity:.0f}+ saat yanıt yok")

        return NegativeSignalResult(total_penalty=total_penalty, signals=signals)


def compute_seasonal_modifier(month: int | None = None) -> int:
    """
    Cyprus/KKTC real-estate seasonality modifier (-5..+5).

    Tourism-driven rhythm:
      - May-Sep: peak buying window (visitors on-site, warm weather viewings) → +3
      - Apr, Oct: shoulder season → +2
      - Mar, Nov: research/planning uptick → +1
      - Dec-Feb: slowdown → -2
    """
    if month is None:
        month = datetime.utcnow().month

    if month in (5, 6, 7, 8, 9):
        return 3
    if month in (4, 10):
        return 2
    if month in (3, 11):
        return 1
    return -2
