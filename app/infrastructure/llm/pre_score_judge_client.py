"""Pre-score judge client — Groq-based single-persona extraction.

Phase 5 multilingual: ONE EN-source template + runtime language injection.
Modern multilingual LLMs handle 15 locales with the same calibrated prompt;
the system prompt's "Output Language" block tells the model to respond in the
lead's language. ``language`` parameter flows from worker → service →
ensemble → here.

Ensemble orchestrator (app/application/scoring/pre_score_ensemble.py) calls
``run_pre_score_persona()`` for 3 personas in parallel (asyncio.gather) and
performs median + aggregation.

Response parsing: 3-tier fallback — direct JSON → ```json code block → first
{...} block. Pydantic validation is mandatory.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal

import structlog
from circuitbreaker import circuit
from groq import APIStatusError, APITimeoutError, AsyncGroq
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.domain.conversation.i18n import normalize_language, resolve_language
from app.domain.conversation.templates.registry import TemplateRegistry
from app.domain.scoring.pre_score_judgment import PreScoreJudgmentResult

log = structlog.get_logger(__name__)

PersonaName = Literal["skeptic", "neutral", "opportunity"]


def _get_judge_client() -> AsyncGroq:
    """Shared Groq client (same API key as qualification judge, same rate limiter)."""
    from app.infrastructure.llm.groq_client import get_groq_client
    return get_groq_client()


def _build_messages(
    persona: PersonaName,
    lead_json: dict[str, Any],
    osint_json: dict[str, Any],
    ideal_customer_profile: str,
    sector: str,
    language: str,
) -> list[dict[str, str]]:
    """Assemble system + user messages for one persona in the lead's language."""
    templates = TemplateRegistry.get_pre_score_templates(language=language, sector=sector)
    persona_text = templates.persona_labels[persona]
    bcp47, language_name = resolve_language(language)

    icp = ideal_customer_profile or (
        "(ICP not defined — assume general construction/real-estate buyer)"
    )
    system_prompt = templates.system_template.format(
        persona=persona_text,
        ideal_customer_profile=icp,
        sector=sector,
        language_name=language_name,
        language_code=bcp47,
    )
    user_prompt = templates.user_template.format(
        lead_json=json.dumps(lead_json, ensure_ascii=False, indent=2),
        osint_json=json.dumps(osint_json, ensure_ascii=False, indent=2),
        few_shots_block=templates.few_shots_block,
        output_schema=templates.output_schema,
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _parse_judgment(text: str) -> PreScoreJudgmentResult:
    """3-tier JSON extraction + Pydantic validation."""
    errors: list[str] = []

    try:
        data = json.loads(text)
        return PreScoreJudgmentResult.model_validate(data)
    except json.JSONDecodeError as exc:
        errors.append(f"direct: {exc}")
    except ValidationError as exc:
        errors.append(f"direct validate: {exc.errors()[:3]}")

    code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_block:
        try:
            data = json.loads(code_block.group(1))
            return PreScoreJudgmentResult.model_validate(data)
        except json.JSONDecodeError as exc:
            errors.append(f"code_block: {exc}")
        except ValidationError as exc:
            errors.append(f"code_block validate: {exc.errors()[:3]}")

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            data = json.loads(text[brace_start : brace_end + 1])
            return PreScoreJudgmentResult.model_validate(data)
        except json.JSONDecodeError as exc:
            errors.append(f"brace: {exc}")
        except ValidationError as exc:
            errors.append(f"brace validate: {exc.errors()[:3]}")

    raise ValueError(
        f"Failed to parse pre-score judgment: {errors}; "
        f"raw response (first 300 chars): {text[:300]}"
    )


@circuit(
    failure_threshold=15,
    recovery_timeout=20,
    expected_exception=(APIStatusError, APITimeoutError),
)
@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((APIStatusError, APITimeoutError, ValidationError)),
    reraise=True,
)
async def run_pre_score_persona(
    *,
    persona: PersonaName,
    lead_json: dict[str, Any],
    osint_json: dict[str, Any],
    ideal_customer_profile: str = "",
    sector: str = "construction",
    language: str = "en",
    model_override: str | None = None,
    max_tokens_override: int | None = None,
    timeout_override: float | None = None,
) -> PreScoreJudgmentResult:
    """Run a single persona's Groq call + parse, in the lead's language.

    Args:
        persona: "skeptic" | "neutral" | "opportunity"
        lead_json: form data (name, email, phone, city, notes, ...)
        osint_json: OSINTProfile.to_dict() output
        ideal_customer_profile: tenant.config ICP text
        sector: "construction" | "real_estate" (set by tenant)
        language: BCP-47 short code, e.g. "tr", "en", "de" — falls back to "en"

    Returns:
        PreScoreJudgmentResult — Pydantic validated.

    Raises:
        ValueError — parse failed (malformed LLM response)
        APIStatusError / APITimeoutError — caught by circuit breaker
    """
    settings = get_settings()
    client = _get_judge_client()
    model = model_override or settings.qualification_judge_model
    max_tokens = max_tokens_override or getattr(
        settings, "pre_score_judge_max_tokens", 2048,
    )
    resolved_language = normalize_language(language)
    templates = TemplateRegistry.get_pre_score_templates(
        language=resolved_language, sector=sector,
    )
    temperature = templates.persona_temperatures[persona]

    messages = _build_messages(
        persona=persona,
        lead_json=lead_json,
        osint_json=osint_json,
        ideal_customer_profile=ideal_customer_profile,
        sector=sector,
        language=resolved_language,
    )

    from app.infrastructure.llm.groq_rate_limiter import acquire
    if not await acquire(estimated_tokens=5000, priority="prescore"):
        raise RuntimeError("Groq rate limit — pre-score judge throttled")

    extra_kwargs: dict[str, Any] = {}
    if "gpt-oss" in model or "compound" in model:
        extra_kwargs["reasoning_effort"] = "low"

    start = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
            response_format={"type": "json_object"},
            **extra_kwargs,
        )
        content = response.choices[0].message.content or ""
        result = _parse_judgment(content)

        if resolved_language == "tr":
            _check_diacritics(persona, result)

        elapsed = time.monotonic() - start
        log.info(
            "pre_score_persona_completed",
            persona=persona,
            language=resolved_language,
            direct_score=result.direct_score,
            extraction_confidence=result.extraction_confidence,
            elapsed_s=round(elapsed, 2),
        )
        return result

    except Exception as exc:
        elapsed = time.monotonic() - start
        log.warning(
            "pre_score_persona_failed",
            persona=persona,
            language=resolved_language,
            error=str(exc),
            elapsed_s=round(elapsed, 2),
        )
        raise


_TR_DIACRITICS: frozenset[str] = frozenset("çşğıİüöÇŞĞÜÖ")


def _check_diacritics(persona: str, result: PreScoreJudgmentResult) -> None:
    """TR-only soft check — warn + metric when Turkish fields come back stripped
    of diacritics. Other locales (EN/DE/FR/etc.) skip this gate via the caller.
    """
    sc = result.sales_context
    for field_name, txt in (
        ("who_they_are", sc.who_they_are or ""),
        ("company_or_buyer_profile", sc.company_or_buyer_profile or ""),
        ("recommended_opening", sc.recommended_opening or ""),
    ):
        if len(txt) < 100:
            continue
        if any(c in _TR_DIACRITICS for c in txt):
            continue
        log.warning(
            "pre_score_diacritics_missing",
            persona=persona,
            field=field_name,
            sample=txt[:120],
        )
        try:
            from app.metrics import PRESCORE_DIACRITICS_MISSING_TOTAL
            PRESCORE_DIACRITICS_MISSING_TOTAL.labels(field=field_name).inc()
        except (ImportError, AttributeError):
            pass
