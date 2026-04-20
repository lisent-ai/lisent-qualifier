"""
Per-message signal analysis pipeline.

Combines behavioral signals, rule-based detection (existing analyzers),
and smart extraction trigger decisions. Pure domain logic — no I/O.

Runs on EVERY inbound user message, <10ms latency, zero LLM cost.
"""
from __future__ import annotations

import re

from app.domain.scoring.signals.base import MessageAnalysisResult
from app.domain.scoring.signals.registry import SignalRegistry

# ── CHAMP dimension keyword maps (Turkish + English) ────────────────────────

_CHAMP_KEYWORDS: dict[str, list[str]] = {
    "challenges": [
        # TR
        "proje", "insaat", "inşaat", "villa", "otel", "rezidans", "konut",
        "kat", "daire", "metrekare", "m2", "arsa", "arazi", "renovasyon",
        "tadilat", "restorasyon", "depo", "fabrika", "ofis", "plaza",
        "havuz", "oda", "salon",
        # EN
        "project", "construction", "building", "floor", "sqm",
        "renovation", "warehouse", "factory", "office", "pool", "room",
    ],
    "authority": [
        # TR
        "karar", "sahip", "patron", "mudur", "müdür", "yonetici",
        "yönetici", "sirket", "şirket", "firma", "ben karar",
        "biz karar", "ortaklarim", "ortaklarım", "komite",
        # EN
        "decision", "owner", "manager", "director", "ceo", "company",
        "i decide", "we decide", "board", "committee", "partner",
    ],
    "money": [
        # TR
        "butce", "bütçe", "fiyat", "maliyet", "milyon", "bin",
        "tl", "dolar", "euro", "metrekare fiyat", "m2 fiyat",
        "odeme", "ödeme", "taksit", "vade", "kapora", "depozito",
        # EN
        "budget", "price", "cost", "million", "thousand", "usd",
        "eur", "payment", "installment", "deposit",
    ],
    "prioritization": [
        # TR
        "acil", "hemen", "bu ay", "bu hafta", "gelecek ay",
        "ne zaman", "zaman", "tarih", "deadline", "ihale",
        "ruhsat", "izin", "sezon", "yaz", "kis", "kış",
        "bahar", "sonbahar", "planliyoruz", "planlıyoruz",
        # EN
        "urgent", "asap", "immediately", "this month", "this week",
        "next month", "when", "timeline", "deadline", "permit",
        "season", "planning",
    ],
}

# Patterns that indicate the message contains numeric data (budget, timeline, etc.)
_NUMERIC_CONTEXT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\d+\s*(milyon|million|m|tl|dolar|euro|usd|eur|bin)\b", re.IGNORECASE),
    re.compile(r"\d+\s*(kat|floor|oda|room|m2|metrekare|daire)\b", re.IGNORECASE),
    re.compile(r"\d+\s*(ay|month|hafta|week|gün|gun|day)\b", re.IGNORECASE),
]


class MessageAnalyzer:
    """
    Per-message signal analysis pipeline.
    Combines behavioral + rule-based signals + optional LLM classification.

    LLM classification (if provided) overrides rule-based intent/sentiment/value
    for higher accuracy. Behavioral signals are always computed locally.
    """

    def analyze(
        self,
        message: str,
        messages: list[dict],
        msg_count: int,
        language: str = "tr",
        sector: str = "construction",
        extract_every_n: int = 3,
        min_message_length: int = 15,
        llm_classification: object | None = None,
    ) -> MessageAnalysisResult:
        conversation_stage = self._compute_stage(msg_count)
        response_time = self._compute_response_time(messages)
        is_follow_up = self._check_follow_up(messages)

        # Rule-based signal detection (always runs as baseline)
        sentiment_analyzer = SignalRegistry.get_sentiment_analyzer(language)
        buying_detector = SignalRegistry.get_buying_signal_detector(language)

        sentiment_result = sentiment_analyzer.analyze(message)
        intent_result = sentiment_analyzer.detect_intent(message)
        buying_signals_rules = buying_detector.detect(message)
        negative_signals_rules = self._detect_negative_signals(message, language)
        champ_dims_rules = self._detect_champ_dimensions(message)

        # LLM classification overrides (if available)
        if llm_classification is not None:
            llm = llm_classification
            intent = getattr(llm, "intent", "") or self._map_intent(
                intent_result, buying_signals_rules, negative_signals_rules,
                sentiment_result, conversation_stage,
            )
            sentiment = getattr(llm, "sentiment", "") or sentiment_result.label
            information_value = getattr(llm, "information_value", "") or self._assess_information_value(
                message, buying_signals_rules, intent_result, sentiment_result, min_message_length,
            )
            # Merge: union of LLM + rule signals (LLM catches what rules miss, rules catch what LLM misses)
            buying_signals = list(set(buying_signals_rules + getattr(llm, "buying_signals", [])))
            negative_signals = list(set(negative_signals_rules + getattr(llm, "negative_signals", [])))
            champ_dims = list(set(champ_dims_rules + [d for d in getattr(llm, "champ_dimensions", []) if d in ("challenges", "authority", "money", "prioritization")]))
            classification_source = getattr(llm, "source", "llm")
        else:
            # Pure rule-based
            information_value = self._assess_information_value(
                message, buying_signals_rules, intent_result, sentiment_result, min_message_length,
            )
            intent = self._map_intent(
                intent_result, buying_signals_rules, negative_signals_rules,
                sentiment_result, conversation_stage,
            )
            sentiment = sentiment_result.label
            buying_signals = buying_signals_rules
            negative_signals = negative_signals_rules
            champ_dims = champ_dims_rules
            classification_source = "rules"

        # Smart trigger decision (uses merged signals)
        should_trigger, trigger_reason = self._decide_trigger(
            msg_count=msg_count,
            extract_every_n=extract_every_n,
            information_value=information_value,
            buying_signals=buying_signals,
            intent=intent,
            champ_dims=champ_dims,
        )

        return MessageAnalysisResult(
            intent=intent,
            sentiment=sentiment,
            information_value=information_value,
            buying_signals=buying_signals,
            negative_signals=negative_signals,
            champ_dimensions=champ_dims,
            response_time_seconds=response_time,
            message_length=len(message),
            is_follow_up=is_follow_up,
            should_trigger_extraction=should_trigger,
            trigger_reason=trigger_reason,
            conversation_stage=conversation_stage,
            classification_source=classification_source,
        )

    # ── Behavioral analysis ─────────────────────────────────────────────────

    @staticmethod
    def _compute_stage(msg_count: int) -> str:
        if msg_count <= 3:
            return "early"
        if msg_count <= 6:
            return "mid"
        return "late"

    @staticmethod
    def _compute_response_time(messages: list[dict]) -> float | None:
        """Time in seconds between last assistant message and current user message."""
        if len(messages) < 2:
            return None
        last_msg = messages[-1]
        # Find the previous assistant message
        for m in reversed(messages[:-1]):
            if m.get("role") == "assistant" and m.get("ts") and last_msg.get("ts"):
                gap = last_msg["ts"] - m["ts"]
                return gap if gap > 0 else None
        return None

    @staticmethod
    def _check_follow_up(messages: list[dict]) -> bool:
        """Check if user sent consecutive messages without assistant reply."""
        if len(messages) < 2:
            return False
        return (
            messages[-1].get("role") == "user"
            and messages[-2].get("role") == "user"
        )

    # ── Signal detection ────────────────────────────────────────────────────

    @staticmethod
    def _detect_negative_signals(message: str, language: str) -> list[str]:
        """Quick per-message negative signal check."""
        lower = message.lower()
        signals: list[str] = []

        if language == "tr":
            patterns = {
                "sadece araştırma": [
                    "sadece bakiyorum", "sadece bakıyorum",
                    "sadece arastiriyorum", "sadece araştırıyorum",
                    "merak ettim",
                ],
                "belirsiz zaman": [
                    "belki gelecek yil", "belki gelecek yıl",
                    "ileride belki", "ilerde belki",
                    "henuz karar vermedim", "henüz karar vermedim",
                    "su an degil", "şu an değil",
                ],
                "yetki_yok": [
                    "patronuma soracagim", "patronuma soracağım",
                    "komiteye sunacagim", "komiteye sunacağım",
                ],
                "fiyat_karsilastirma": [
                    "baska firmalar", "başka firmalar",
                    "rakip firma", "diger teklifler", "diğer teklifler",
                ],
            }
        else:
            patterns = {
                "just_researching": [
                    "just looking", "just browsing", "exploring options",
                ],
                "vague_timeline": [
                    "maybe next year", "not sure yet", "no timeline",
                ],
                "no_authority": [
                    "need to check with my boss", "committee decision",
                ],
                "price_shopping": [
                    "other companies", "competitor quotes",
                ],
            }

        for signal_name, keywords in patterns.items():
            if any(kw in lower for kw in keywords):
                signals.append(signal_name)

        return signals

    @staticmethod
    def _detect_champ_dimensions(message: str) -> list[str]:
        """Detect which CHAMP dimensions this message touches."""
        lower = message.lower()
        dims: list[str] = []

        for dim, keywords in _CHAMP_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                dims.append(dim)

        # Numeric data with context → likely money or prioritization
        if not dims:
            for pattern in _NUMERIC_CONTEXT_PATTERNS:
                if pattern.search(message):
                    dims.append("money")
                    break

        return dims

    # ── Information value assessment ────────────────────────────────────────

    @staticmethod
    def _assess_information_value(
        message: str,
        buying_signals: list[str],
        intent_result,
        sentiment_result,
        min_length: int,
    ) -> str:
        msg_stripped = message.strip()
        msg_len = len(msg_stripped)
        lower = msg_stripped.lower()

        low_value_phrases = (
            "hangi şirketten",
            "hangi sirketten",
            "hangi şirket",
            "hangi sirket",
            "iyiyim",
            "teşekkür",
            "tesekkur",
            "nasılsınız",
            "nasilsiniz",
            "yok",
            "yani",
            "olur",
            "tamam",
            "evet",
            "bilmiyorum",
            "çok bilmiyorum",
            "cok bilmiyorum",
            "bilgi sahibi değilim",
            "bilgi sahibi degilim",
        )
        if any(phrase in lower for phrase in low_value_phrases) and msg_len < 80:
            return "low"

        # Has buying signals or high intent → high
        if buying_signals or intent_result.intent == "high_intent":
            return "high"

        # Has numeric data with context (budget, measurements, dates)
        for pattern in _NUMERIC_CONTEXT_PATTERNS:
            if pattern.search(message):
                return "high"

        # Short messages with no signals → low
        if msg_len < min_length and intent_result.intent == "neutral":
            return "low"

        # Medium+ length message → medium
        if msg_len > 50:
            return "medium"

        # Has some content but nothing special
        if msg_len >= min_length:
            return "medium"

        return "low"

    # ── Intent mapping ──────────────────────────────────────────────────────

    @staticmethod
    def _map_intent(
        intent_result,
        buying_signals: list[str],
        negative_signals: list[str],
        sentiment_result,
        stage: str,
    ) -> str:
        # Buying signals always win
        if buying_signals:
            return "buying_signal"

        if intent_result.intent == "high_intent":
            return "buying_signal"

        # Late stage + price complaint = NEGOTIATION (positive signal!)
        if stage == "late" and sentiment_result.label == "negative":
            price_keywords = {"pahali", "pahalı", "çok pahalı", "cok pahali"}
            if price_keywords & set(sentiment_result.matched_keywords):
                return "negotiation"

        if intent_result.intent == "off_topic":
            return "small_talk"

        if negative_signals:
            # Early stage negatives might just be cultural openers
            if stage == "early" and "sadece araştırma" in negative_signals:
                return "info_seeking"  # not a real objection yet
            return "objection"

        if intent_result.intent == "low_intent":
            return "info_seeking"

        return "info_seeking"

    # ── Smart trigger decision ──────────────────────────────────────────────

    @staticmethod
    def _decide_trigger(
        msg_count: int,
        extract_every_n: int,
        information_value: str,
        buying_signals: list[str],
        intent: str,
        champ_dims: list[str],
    ) -> tuple[bool, str]:
        """Decide whether to trigger CHAMP extraction for this message."""
        # Early conversation guard:
        # Avoid triggering extraction in the first 3 user messages unless
        # the message contains very strong purchase/financial signals.
        if msg_count <= 3 and information_value != "high" and intent != "buying_signal":
            return False, "early_conversation_guard"

        # Safety net: ALWAYS trigger on periodic N
        if msg_count % extract_every_n == 0:
            return True, f"periodic_{extract_every_n}"

        # HIGH VALUE message → trigger immediately
        if information_value == "high":
            reason = "high_value"
            if buying_signals:
                reason = f"buying_signal:{buying_signals[0]}"
            return True, reason

        # Buying signal intent → trigger
        if intent == "buying_signal":
            return True, "buying_signal_intent"

        # Message touches 2+ CHAMP dimensions → rich info, extract
        if len(champ_dims) >= 2:
            return True, f"multi_champ:{'+'.join(champ_dims)}"

        # LOW VALUE → skip extraction (save LLM cost)
        if information_value == "low":
            return False, "low_value_skip"

        # MEDIUM value but only 1 dimension → wait for periodic
        return False, "medium_wait_periodic"
