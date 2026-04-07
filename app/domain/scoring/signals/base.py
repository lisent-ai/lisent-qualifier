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


@dataclass(frozen=True)
class MessageAnalysisResult:
    """Per-message signal analysis result. Aggregates behavioral + rule-based layers."""

    # Intent classification
    intent: str  # buying_signal | info_seeking | objection | small_talk | negotiation | urgency
    sentiment: str  # positive | neutral | negative
    information_value: str  # high | medium | low

    # Detected signals
    buying_signals: list[str] = field(default_factory=list)
    negative_signals: list[str] = field(default_factory=list)

    # CHAMP dimensions this message touches
    champ_dimensions: list[str] = field(default_factory=list)

    # Behavioral
    response_time_seconds: float | None = None
    message_length: int = 0
    is_follow_up: bool = False  # user sent another msg before we replied

    # Trigger decisions
    should_trigger_extraction: bool = False
    trigger_reason: str = "no_trigger"

    # Conversation stage context
    conversation_stage: str = "early"  # early (1-3) | mid (4-6) | late (7+)

    # Classification source
    classification_source: str = "rules"  # groq | local_llm | rules

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "sentiment": self.sentiment,
            "information_value": self.information_value,
            "buying_signals": self.buying_signals,
            "negative_signals": self.negative_signals,
            "champ_dimensions": self.champ_dimensions,
            "response_time_seconds": self.response_time_seconds,
            "message_length": self.message_length,
            "is_follow_up": self.is_follow_up,
            "should_trigger_extraction": self.should_trigger_extraction,
            "trigger_reason": self.trigger_reason,
            "conversation_stage": self.conversation_stage,
            "classification_source": self.classification_source,
        }
