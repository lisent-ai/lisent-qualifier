"""
Abstract base classes for language-specific and sector-specific signal analyzers.

Implementations live under signals/languages/ and signals/sectors/.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SentimentResult:
    score: float  # -1.0 (very negative) to +1.0 (very positive)
    label: str  # "positive" | "neutral" | "negative"
    matched_keywords: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntentResult:
    intent: str  # "high_intent" | "low_intent" | "neutral" | "off_topic"
    matched_keywords: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NegativeSignalResult:
    total_penalty: int  # <= 0
    signals: list[str] = field(default_factory=list)  # human-readable descriptions


class SentimentAnalyzer(ABC):
    """Language-specific keyword-based sentiment analysis. Zero latency."""

    @abstractmethod
    def analyze(self, text: str) -> SentimentResult:
        ...

    @abstractmethod
    def detect_intent(self, text: str) -> IntentResult:
        ...


class NegativeSignalDetector(ABC):
    """Detects disqualification signals from conversation messages."""

    @abstractmethod
    def detect(
        self,
        messages: list[dict],
        hours_since_last_activity: float = 0.0,
    ) -> NegativeSignalResult:
        ...


class BuyingSignalDetector(ABC):
    """Detects positive buying signals from a single message."""

    @abstractmethod
    def detect(self, text: str) -> list[str]:
        """Returns list of detected buying signal descriptions."""
        ...
