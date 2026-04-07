"""
Per-message LLM classification with 3-tier fallback.

Priority order:
  1. Groq API (most accurate, 120B model, paid tier = 500K RPM)
  2. Local LLM (Qwen 3.5-4B, zero cost, when available)
  3. Returns None → caller falls back to rule-based

Includes Redis-based rate limit tracking to stay within TPM budget.
"""
import json
import structlog
from typing import Any

from app.config import get_settings

log = structlog.get_logger(__name__)

_CLASSIFICATION_PROMPT = """\
You are classifying messages in a construction/real estate lead qualification chat.

Stage: {stage}
Context:
{context}

Message: "{message}"

CLASSIFICATION RULES (follow these exactly):

intent - pick ONE:
- "buying_signal": ANY request for meeting/call/contract/viewing/appointment/Zoom, price quote requests, asking about payment terms, "gorusme istiyorum", "sozlesme", "randevu", "teklif", "ne zaman baslayabiliriz"
- "urgency": "acil", "hemen", "bu ay icinde"
- "negotiation": price complaints in late stage ("pahali", "cok pahali", "butce asiliyor") — this is positive, means they want to buy but negotiate
- "objection": price complaints in early/mid stage, "ilgilenmiyorum", "vazgectim"
- "small_talk": greetings, "evet", "tamam", "ok", off-topic
- "info_seeking": general questions, asking for information (default)

information_value - pick ONE:
- "high": contains budget numbers, project specs (rooms, sqm, location), timeline, or buying signals
- "low": very short (<15 chars), single words, greetings, "evet"/"tamam"/"ok"
- "medium": everything else

Examples:
- "Gorusme istiyorum" -> {{"intent":"buying_signal","sentiment":"positive","information_value":"high","buying_signals":["meeting_request"],"negative_signals":[],"champ_dimensions":[]}}
- "Sozlesme sartlari nedir?" -> {{"intent":"buying_signal","sentiment":"positive","information_value":"high","buying_signals":["contract_inquiry"],"negative_signals":[],"champ_dimensions":["money"]}}
- "3 katli otel, 40 oda, butce 8M" -> {{"intent":"info_seeking","sentiment":"positive","information_value":"high","buying_signals":[],"negative_signals":[],"champ_dimensions":["challenges","money"]}}
- "Pahali" (late stage) -> {{"intent":"negotiation","sentiment":"negative","information_value":"medium","buying_signals":[],"negative_signals":[],"champ_dimensions":["money"]}}
- "Evet" -> {{"intent":"small_talk","sentiment":"positive","information_value":"low","buying_signals":[],"negative_signals":[],"champ_dimensions":[]}}
- "Sadece bakiyorum" -> {{"intent":"info_seeking","sentiment":"neutral","information_value":"low","buying_signals":[],"negative_signals":["just_researching"],"champ_dimensions":[]}}

Return ONLY the JSON object."""


class LLMClassificationResult:
    """Parsed LLM classification result."""
    __slots__ = ("intent", "sentiment", "information_value",
                 "buying_signals", "negative_signals", "champ_dimensions",
                 "source")

    def __init__(
        self,
        intent: str = "info_seeking",
        sentiment: str = "neutral",
        information_value: str = "medium",
        buying_signals: list[str] | None = None,
        negative_signals: list[str] | None = None,
        champ_dimensions: list[str] | None = None,
        source: str = "rules",
    ):
        self.intent = intent
        self.sentiment = sentiment
        self.information_value = information_value
        self.buying_signals = buying_signals or []
        self.negative_signals = negative_signals or []
        self.champ_dimensions = champ_dimensions or []
        self.source = source


def _build_context(messages: list[dict], limit: int = 3) -> str:
    if not messages:
        return "(no previous messages)"
    recent = messages[-limit:]
    lines = []
    for m in recent:
        role = m.get("role", "?").upper()
        content = m.get("content", "")[:200]
        lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(no previous messages)"


_VALID_INTENTS = {"buying_signal", "info_seeking", "objection", "small_talk", "negotiation", "urgency"}
_VALID_SENTIMENTS = {"positive", "neutral", "negative"}
_VALID_VALUES = {"high", "medium", "low"}
_VALID_DIMS = {"challenges", "authority", "money", "prioritization"}


def _parse_response(raw: str) -> LLMClassificationResult:
    """Parse LLM JSON response into classification result. Tolerant of encoding issues."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        import re
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1)
    brace_start = cleaned.find("{")
    brace_end = cleaned.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        cleaned = cleaned[brace_start:brace_end + 1]

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fix common issues: trailing commas, unescaped chars
        import re
        fixed = re.sub(r",\s*}", "}", cleaned)
        fixed = re.sub(r",\s*]", "]", fixed)
        # Replace smart quotes
        fixed = fixed.replace("\u201c", '"').replace("\u201d", '"')
        fixed = fixed.replace("\u2018", "'").replace("\u2019", "'")
        data = json.loads(fixed)

    return LLMClassificationResult(
        intent=data.get("intent") if data.get("intent") in _VALID_INTENTS else "info_seeking",
        sentiment=data.get("sentiment") if data.get("sentiment") in _VALID_SENTIMENTS else "neutral",
        information_value=data.get("information_value") if data.get("information_value") in _VALID_VALUES else "medium",
        buying_signals=[s for s in data.get("buying_signals", []) if isinstance(s, str)],
        negative_signals=[s for s in data.get("negative_signals", []) if isinstance(s, str)],
        champ_dimensions=[d for d in data.get("champ_dimensions", []) if d in _VALID_DIMS],
    )


async def classify_with_groq(
    message: str,
    messages: list[dict],
    stage: str,
) -> LLMClassificationResult | None:
    """Tier 1: Groq API classification (120B, most accurate)."""
    try:
        from app.infrastructure.llm.groq_rate_limiter import acquire
        if not await acquire(estimated_tokens=300, priority="classify"):
            log.debug("classify_groq_rate_limited")
            return None

        from app.infrastructure.llm.groq_client import get_groq_client
        settings = get_settings()
        client = get_groq_client()

        prompt = _CLASSIFICATION_PROMPT.format(
            stage=stage,
            context=_build_context(messages),
            message=message[:500],
        )

        # llama-3.3-70b is significantly better at Turkish understanding
        classify_model = "llama-3.3-70b-versatile"
        response = await client.chat.completions.create(
            model=classify_model,
            messages=[
                {"role": "system", "content": "You classify messages for a Turkish/English construction lead chat. Return ONLY a single-line JSON object. All values in English. No Turkish characters in output. No markdown."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
            temperature=0,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content or "{}"
        result = _parse_response(raw)
        result.source = "groq"
        return result
    except Exception as exc:
        error_str = str(exc)[:100]
        if "429" in error_str or "rate_limit" in error_str:
            log.warning("groq_classify_rate_limited", error=error_str)
        else:
            log.warning("groq_classify_failed", error=error_str)
        return None


async def classify_with_local_llm(
    message: str,
    messages: list[dict],
    stage: str,
) -> LLMClassificationResult | None:
    """Tier 2: Local LLM classification (Qwen 3.5-4B, fast & free)."""
    try:
        from app.infrastructure.llm.local_llm_client import _call_local_llm

        prompt = _CLASSIFICATION_PROMPT.format(
            stage=stage,
            context=_build_context(messages),
            message=message[:500],
        )

        raw = await _call_local_llm(
            [{"role": "user", "content": prompt}],
            timeout=5.0,
            max_tokens=200,
        )

        result = _parse_response(raw)
        result.source = "local_llm"
        return result
    except Exception as exc:
        log.debug("local_llm_classify_unavailable", error=str(exc)[:80])
        return None


async def classify_message(
    message: str,
    messages: list[dict],
    stage: str,
) -> LLMClassificationResult | None:
    """
    Classify a user message with 3-tier fallback:
      1. Groq (accurate, paid)
      2. Local LLM (fast, free)
      3. None → caller uses rule-based

    Returns None only if both LLM tiers fail.
    """
    # Tier 1: Groq (primary — most accurate)
    result = await classify_with_groq(message, messages, stage)
    if result is not None:
        return result

    # Tier 2: Local LLM (fallback — fast & free)
    result = await classify_with_local_llm(message, messages, stage)
    if result is not None:
        return result

    # Tier 3: None — caller uses rule-based
    log.info("classify_all_tiers_failed")
    return None
