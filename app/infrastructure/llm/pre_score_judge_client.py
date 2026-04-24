"""Pre-score judge client — Groq-based single-persona extraction.

Phase 2. Mirrors `qualification_judge_client.py` structure. Difference: bu
judge chat-öncesi intake'te çalışır (conversation_history yok), lead form +
OSINT profile + persona'ya bağlı system prompt alır.

Ensemble orchestrator (app/application/scoring/pre_score_ensemble.py) bu
modülün `run_pre_score_persona()` fonksiyonunu 3 paralel persona ile çağırır
(asyncio.gather) ve median + aggregation yapar.

Response parsing: 3-tier fallback — direct JSON → ```json code block → first
{...} block. Pydantic validation zorunlu.
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
from app.domain.conversation.templates.tr.pre_score_judge import (
    FEW_SHOTS_TR_CONSTRUCTION,
    OUTPUT_SCHEMA_EXAMPLE,
    PERSONA_LABELS,
    PERSONA_TEMPERATURES,
    PRE_SCORE_JUDGE_SYSTEM_TEMPLATE,
    PRE_SCORE_JUDGE_USER_TEMPLATE,
)
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
) -> list[dict[str, str]]:
    """Assemble system + user messages for one persona."""
    persona_text = PERSONA_LABELS[persona]
    icp = ideal_customer_profile or (
        "(ICP tanımlanmamış — genel inşaat/gayrimenkul kabul et)"
    )
    system_prompt = PRE_SCORE_JUDGE_SYSTEM_TEMPLATE.format(
        persona=persona_text,
        ideal_customer_profile=icp,
        sector=sector,
    )
    user_prompt = PRE_SCORE_JUDGE_USER_TEMPLATE.format(
        lead_json=json.dumps(lead_json, ensure_ascii=False, indent=2),
        osint_json=json.dumps(osint_json, ensure_ascii=False, indent=2),
        few_shots_block=FEW_SHOTS_TR_CONSTRUCTION,
        output_schema=OUTPUT_SCHEMA_EXAMPLE,
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _parse_judgment(text: str) -> PreScoreJudgmentResult:
    """3-tier JSON extraction + Pydantic validation.

    1. Direct json.loads
    2. Markdown code block extraction
    3. First brace-block substring
    """
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
    # Tolerate bursts: 3 personas × several concurrent leads can legitimately
    # hit a rate-limit window without the Groq upstream being sick. A higher
    # threshold + shorter recovery rides through reset cycles.
    failure_threshold=15,
    recovery_timeout=20,
    # Only treat *Groq API* failures as circuit signals. RuntimeError from
    # the rate limiter is situational (TPM window saturated) — if every
    # throttled call tripped the breaker, a small burst would knock the
    # whole pipeline out for a minute.
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
    model_override: str | None = None,
    max_tokens_override: int | None = None,
    timeout_override: float | None = None,
) -> PreScoreJudgmentResult:
    """Tek bir persona için Groq call + parse.

    Args:
        persona: "skeptic" | "neutral" | "opportunity"
        lead_json: form verisi (name, email, phone, city, notes, ...)
        osint_json: OSINTProfile.to_dict() çıktısı
        ideal_customer_profile: tenant.config'den ICP metni (Türkçe)
        sector: "construction" | "real_estate" (tenant tarafından set edilir)

    Returns:
        PreScoreJudgmentResult — Pydantic validated.

    Raises:
        ValueError — parse edilemedi (malformed LLM response)
        APIStatusError / APITimeoutError — circuit breaker tarafından yakalanır
    """
    settings = get_settings()
    client = _get_judge_client()
    model = model_override or settings.qualification_judge_model
    max_tokens = max_tokens_override or getattr(
        settings, "pre_score_judge_max_tokens", 2048,
    )
    temperature = PERSONA_TEMPERATURES[persona]

    messages = _build_messages(
        persona=persona,
        lead_json=lead_json,
        osint_json=osint_json,
        ideal_customer_profile=ideal_customer_profile,
        sector=sector,
    )

    # Rate limit acquire — priority="prescore" chat judge'den düşük öncelikli
    from app.infrastructure.llm.groq_rate_limiter import acquire
    if not await acquire(estimated_tokens=5000, priority="prescore"):
        raise RuntimeError("Groq rate limit — pre-score judge throttled")

    # gpt-oss-120b burns tokens on internal reasoning before emitting the
    # structured JSON. With reasoning_effort default the 2048-token budget
    # often overflows before `sales_context` / `intent` / `fit` fields get
    # written, producing partial JSON that fails Pydantic validation.
    # Forcing reasoning_effort="low" cuts the CoT pre-roll so the model
    # spends the budget on the output schema. Only applies to gpt-oss-* /
    # compound models; llama-3.3 ignores the field silently.
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

        # Soft diacritics check — log + metric only, no retry. Some Groq
        # models under low temperature strip Turkish diacritics ("sehir"
        # instead of "şehir") which reads amateurish in the CRM panel.
        # We surface this as observability so we can track % over time;
        # retrying would double-spend Groq tokens for marginal gain.
        _check_diacritics(persona, result)

        elapsed = time.monotonic() - start
        log.info(
            "pre_score_persona_completed",
            persona=persona,
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
            error=str(exc),
            elapsed_s=round(elapsed, 2),
        )
        raise


_TR_DIACRITICS: frozenset[str] = frozenset("çşğıİüöÇŞĞÜÖ")


def _check_diacritics(persona: str, result: PreScoreJudgmentResult) -> None:
    """Warn + metric when sales_context Turkish fields come back stripped of
    diacritics. 100-char floor avoids false positives on short English-like
    segments; any one Turkish diacritic anywhere proves the model produced
    proper Turkish."""
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
            # Counter not wired yet (Track 5.A); swallow silently.
            pass
