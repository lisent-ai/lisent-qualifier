"""
General/default sector — negative signals and seasonality.
Used when no sector-specific implementation is available.
"""
from __future__ import annotations

from app.domain.scoring.signals.base import NegativeSignalDetector, NegativeSignalResult

_JUST_LOOKING: list[str] = [
    "just looking",
    "just browsing",
    "not interested",
    "sadece bakiyorum",
    "sadece bakıyorum",
]


class GeneralNegativeDetector(NegativeSignalDetector):
    def detect(
        self,
        messages: list[dict],
        hours_since_last_activity: float = 0.0,
    ) -> NegativeSignalResult:
        total_penalty = 0
        signals: list[str] = []

        user_msgs = [m for m in messages if m.get("role") == "user"]

        for m in user_msgs:
            text = m.get("content", "").lower()
            if any(p in text for p in _JUST_LOOKING):
                total_penalty += -15
                signals.append("Low purchase intent detected")
                break

        if hours_since_last_activity > 48:
            days = hours_since_last_activity / 24
            decay = min(int(days - 2) * -5, -25)
            total_penalty += decay
            signals.append(f"No response for {hours_since_last_activity:.0f}+ hours")

        return NegativeSignalResult(total_penalty=total_penalty, signals=signals)


def compute_seasonal_modifier(month: int | None = None) -> int:
    """No seasonal effect for general sector."""
    return 0
