"""
Qualification Judge client — Groq-based holistic lead scoring.

Separate from groq_client.py:
  - Non-streaming, structured JSON output
  - temperature=0 for consistency
  - Separate circuit breaker (judge failures don't affect chat)
  - Self-consistency support for borderline scores
"""
import asyncio
import json
import re
import time
from typing import Any

import structlog
from circuitbreaker import circuit
from groq import AsyncGroq, APIStatusError, APITimeoutError
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import get_settings
from app.infrastructure.llm.schemas import QualificationJudgmentResult
from app.metrics import JUDGE_EXTRACTIONS, JUDGE_LATENCY, JUDGE_SELF_CONSISTENCY

log = structlog.get_logger(__name__)


def _get_judge_client() -> AsyncGroq:
    """Reuse the shared Groq client (same API key, separate circuit breaker)."""
    from app.infrastructure.llm.groq_client import get_groq_client
    return get_groq_client()


def _build_judge_prompt(
    conversation_text: str,
    lead_json: dict[str, Any],
    current_judgment_json: dict[str, Any] | None,
    company_config: dict[str, Any] | None,
    language: str,
    sector: str,
) -> list[dict[str, str]]:
    """Build messages array for the qualification judge."""
    from app.domain.conversation.prompts import build_qualification_judge_prompt

    system_prompt = build_qualification_judge_prompt(
        conversation_history=conversation_text,
        lead_json=lead_json,
        current_judgment_json=current_judgment_json,
        company_config=company_config,
    )

    return [{"role": "system", "content": system_prompt}]


def _parse_judgment(text: str) -> QualificationJudgmentResult:
    """Parse LLM response into QualificationJudgmentResult."""
    # Try direct JSON parse first
    try:
        data = json.loads(text)
        return QualificationJudgmentResult.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        pass

    # Try extracting JSON from markdown code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return QualificationJudgmentResult.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

    # Last resort: find first { ... } block
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            data = json.loads(text[brace_start : brace_end + 1])
            return QualificationJudgmentResult.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

    raise ValueError(f"Failed to parse qualification judgment from LLM response: {text[:200]}")


@circuit(
    failure_threshold=3,
    recovery_timeout=60,
    expected_exception=(APIStatusError, APITimeoutError, Exception),
)
@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((APIStatusError, APITimeoutError, ValidationError)),
    reraise=True,
)
async def run_qualification_judge(
    conversation_text: str,
    lead_json: dict[str, Any],
    current_judgment_json: dict[str, Any] | None = None,
    company_config: dict[str, Any] | None = None,
    language: str = "tr",
    sector: str = "construction",
) -> QualificationJudgmentResult:
    """Single-pass qualification judgment via Groq API."""
    settings = get_settings()
    client = _get_judge_client()
    messages = _build_judge_prompt(
        conversation_text, lead_json, current_judgment_json,
        company_config, language, sector,
    )

    from app.infrastructure.llm.groq_rate_limiter import acquire
    if not await acquire(estimated_tokens=3000, priority="judge"):
        raise RuntimeError("Groq rate limit — judge extraction throttled")

    start = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=settings.qualification_judge_model,
            messages=messages,
            max_tokens=settings.qualification_judge_max_tokens,
            temperature=0,
            stream=False,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or ""
        result = _parse_judgment(content)

        elapsed = time.monotonic() - start
        JUDGE_LATENCY.observe(elapsed)
        JUDGE_EXTRACTIONS.labels(success="true", mode="llm_judge").inc()

        log.info(
            "judge_completed",
            holistic_score=result.holistic_score,
            total=result.total,
            confidence=result.confidence,
            elapsed_s=round(elapsed, 2),
        )
        return result

    except Exception:
        elapsed = time.monotonic() - start
        JUDGE_LATENCY.observe(elapsed)
        JUDGE_EXTRACTIONS.labels(success="false", mode="llm_judge").inc()
        raise


async def run_judge_with_self_consistency(
    conversation_text: str,
    lead_json: dict[str, Any],
    current_judgment_json: dict[str, Any] | None = None,
    company_config: dict[str, Any] | None = None,
    language: str = "tr",
    sector: str = "construction",
    num_passes: int = 3,
) -> QualificationJudgmentResult:
    """
    Self-consistency: run N passes concurrently with slight temperature
    variation, then average scores for more robust results.

    Only called for borderline scores (configurable range).
    """
    settings = get_settings()
    client = _get_judge_client()
    messages = _build_judge_prompt(
        conversation_text, lead_json, current_judgment_json,
        company_config, language, sector,
    )

    JUDGE_SELF_CONSISTENCY.inc()

    # Reserve budget for all passes upfront
    from app.infrastructure.llm.groq_rate_limiter import acquire
    if not await acquire(estimated_tokens=3000 * num_passes, priority="judge"):
        raise RuntimeError("Groq rate limit — self-consistency throttled")

    async def _single_pass(temp: float) -> QualificationJudgmentResult:
        response = await client.chat.completions.create(
            model=settings.qualification_judge_model,
            messages=messages,
            max_tokens=settings.qualification_judge_max_tokens,
            temperature=temp,
            stream=False,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        return _parse_judgment(content)

    # Run passes with slight temperature variation for diversity
    temps = [0.0] + [0.3] * (num_passes - 1)
    tasks = [_single_pass(t) for t in temps[:num_passes]]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out failures
    valid: list[QualificationJudgmentResult] = [
        r for r in results if isinstance(r, QualificationJudgmentResult)
    ]

    if not valid:
        log.warning("self_consistency_all_failed", num_passes=num_passes)
        raise ValueError("All self-consistency passes failed")

    if len(valid) == 1:
        return valid[0]

    # Average scores across valid results
    def _avg(values: list[int]) -> int:
        return round(sum(values) / len(values))

    def _avg_f(values: list[float]) -> float:
        return round(sum(values) / len(values), 2)

    # Union negative signals
    all_neg: set[str] = set()
    for r in valid:
        all_neg.update(r.negative_signals)

    # Pick the longest thinking (most detailed reasoning)
    best_thinking = max(valid, key=lambda r: len(r.thinking))

    merged = QualificationJudgmentResult(
        thinking=best_thinking.thinking,
        challenges_score=_avg([r.challenges_score for r in valid]),
        challenges_reasoning=best_thinking.challenges_reasoning,
        challenges_confidence=min(r.challenges_confidence for r in valid),
        authority_score=_avg([r.authority_score for r in valid]),
        authority_reasoning=best_thinking.authority_reasoning,
        authority_confidence=min(r.authority_confidence for r in valid),
        money_score=_avg([r.money_score for r in valid]),
        money_reasoning=best_thinking.money_reasoning,
        money_confidence=min(r.money_confidence for r in valid),
        prioritization_score=_avg([r.prioritization_score for r in valid]),
        prioritization_reasoning=best_thinking.prioritization_reasoning,
        prioritization_confidence=min(r.prioritization_confidence for r in valid),
        holistic_score=_avg([r.holistic_score for r in valid]),
        holistic_reasoning=best_thinking.holistic_reasoning,
        icp_fit_assessment=best_thinking.icp_fit_assessment,
        negative_signals=list(all_neg),
        negative_penalty=min(r.negative_penalty for r in valid),
        negative_reasoning=best_thinking.negative_reasoning,
        sector_qualifiers=best_thinking.sector_qualifiers,
        missing_info=best_thinking.missing_info,
        recommended_next_question=best_thinking.recommended_next_question,
        confidence=best_thinking.confidence,
    )

    log.info(
        "self_consistency_merged",
        num_valid=len(valid),
        num_total=num_passes,
        holistic_scores=[r.holistic_score for r in valid],
        merged_holistic=merged.holistic_score,
    )

    return merged
