"""
Construction sector — negative signals, seasonality, and sector-specific detection.
"""
from __future__ import annotations

import math
from datetime import datetime

from app.domain.scoring.signals.base import NegativeSignalDetector, NegativeSignalResult

# ── Construction-specific negative patterns (Turkish + English) ───────────────

_PRICE_FISHING_PATTERNS: list[str] = [
    "fiyat nedir",
    "fiyat ne kadar",
    "ne kadar tutar",
    "metrekare fiyati",
    "metrekare fiyatı",
    "what's the price",
    "how much does it cost",
    "price per sqm",
]

_JUST_LOOKING_PATTERNS: list[str] = [
    "sadece bakiyorum",
    "sadece bakıyorum",
    "merak ettim",
    "arastiriyorum",
    "araştırıyorum",
    "just looking",
    "just browsing",
    "exploring options",
]

_COMPETITOR_EMPLOYEE_PATTERNS: list[str] = [
    "rakip firma",
    "biz de insaat firmasiyiz",
    "biz de inşaat firmasıyız",
    "i work for a construction company",
    "competitor research",
]


class ConstructionNegativeDetector(NegativeSignalDetector):
    def detect(
        self,
        messages: list[dict],
        hours_since_last_activity: float = 0.0,
    ) -> NegativeSignalResult:
        total_penalty = 0
        signals: list[str] = []

        user_msgs = [m for m in messages if m.get("role") == "user"]

        # ── Price fishing without budget disclosure ──────────────────────
        price_asks = 0
        has_budget_info = False
        for m in user_msgs:
            text = m.get("content", "").lower()
            if any(p in text for p in _PRICE_FISHING_PATTERNS):
                price_asks += 1
            # Rough check for budget disclosure
            if any(
                kw in text
                for kw in [
                    "butce",
                    "bütçe",
                    "budget",
                    "milyon",
                    "million",
                    "bin tl",
                    "tl",
                ]
            ):
                has_budget_info = True

        if price_asks >= 2 and not has_budget_info:
            penalty = min(price_asks * -10, -30)
            total_penalty += penalty
            signals.append(f"Fiyat soruyor ama bütçe vermiyor ({price_asks}x)")

        # ── Just looking / researching ───────────────────────────────────
        for m in user_msgs:
            text = m.get("content", "").lower()
            if any(p in text for p in _JUST_LOOKING_PATTERNS):
                total_penalty += -15
                signals.append("Sadece araştırma amaçlı")
                break

        # ── Competitor employee ──────────────────────────────────────────
        for m in user_msgs:
            text = m.get("content", "").lower()
            if any(p in text for p in _COMPETITOR_EMPLOYEE_PATTERNS):
                total_penalty = -100
                signals = ["Rakip firma çalışanı tespit edildi — diskalifiye"]
                return NegativeSignalResult(total_penalty=total_penalty, signals=signals)

        # -- Inactivity decay (responsive: starts at 2h, not 48h) --
        if hours_since_last_activity > 2:
            if hours_since_last_activity > 48:
                decay = -25  # cap
            elif hours_since_last_activity > 24:
                decay = -15
            elif hours_since_last_activity > 6:
                decay = -10
            else:
                decay = -5  # 2-6 hours
            total_penalty += decay
            signals.append(f"{hours_since_last_activity:.0f}+ saat yanıt yok")

        return NegativeSignalResult(total_penalty=total_penalty, signals=signals)


def compute_seasonal_modifier(month: int | None = None) -> int:
    """
    Returns a seasonal score modifier (-5 to +5) for construction leads.

    Peak construction: March-November
    Planning peak: January-March
    Slowdown: December-February
    """
    if month is None:
        month = datetime.utcnow().month

    # Peak season: construction actively happening
    if month in (4, 5, 6, 7, 8, 9, 10):
        return 3

    # Planning season: budgets being set, leads are serious
    if month in (1, 2, 3):
        return 5

    # November: tail of season
    if month == 11:
        return 0

    # December: slowdown
    return -3
