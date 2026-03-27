"""
httpx async client for local llama.cpp server.
Circuit breaker + tenacity retry + JSON extraction with Pydantic validation.
"""
import json
import re
import structlog
from typing import Any

import httpx
from circuitbreaker import circuit
from pydantic import TypeAdapter, ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import get_settings
from .schemas import BANTExtractionResult, ReasoningReportResult

log = structlog.get_logger(__name__)

_bant_adapter = TypeAdapter(BANTExtractionResult)
_reasoning_adapter = TypeAdapter(ReasoningReportResult)

_HTTP_CLIENT: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        settings = get_settings()
        _HTTP_CLIENT = httpx.AsyncClient(
            base_url=settings.local_llm_url,
            http2=True,
            timeout=httpx.Timeout(connect=5.0, read=90.0, write=10.0, pool=5.0),
        )
    return _HTTP_CLIENT


async def close_http_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT and not _HTTP_CLIENT.is_closed:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


def _extract_json(text: str) -> str:
    """Extract first JSON object from LLM output (handles markdown code blocks)."""
    # Try ```json ... ``` block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    # Try raw JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text


@circuit(failure_threshold=3, recovery_timeout=60, expected_exception=Exception)
async def _call_local_llm(messages: list[dict], timeout: float, max_tokens: int = 1024) -> str:
    """Raw LLM call with circuit breaker."""
    client = get_http_client()
    settings = get_settings()
    payload = {
        "model": settings.local_llm_model,
        "messages": messages,
        "temperature": 0.05,
        "max_tokens": max_tokens,
        "stream": False,
    }
    response = await client.post(
        "/v1/chat/completions",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((httpx.HTTPError, ValidationError)),
    reraise=True,
)
async def extract_bant(conversation_text: str) -> BANTExtractionResult:
    from app.domain.conversation.prompts import build_bant_extraction_prompt

    settings = get_settings()
    prompt = build_bant_extraction_prompt(conversation_text)
    messages = [{"role": "user", "content": prompt}]

    raw = await _call_local_llm(messages, timeout=float(settings.local_llm_timeout_bant))
    json_str = _extract_json(raw)

    try:
        return _bant_adapter.validate_json(json_str)
    except (ValidationError, json.JSONDecodeError) as exc:
        log.warning("bant_validation_failed", error=str(exc), raw=raw[:200])
        raise


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((httpx.HTTPError, ValidationError)),
    reraise=True,
)
async def generate_reasoning_report(
    lead_json: dict[str, Any],
    score: int,
    score_breakdown: dict[str, int],
    bant_json: dict[str, Any] | None = None,
) -> ReasoningReportResult:
    from app.domain.conversation.prompts import build_reasoning_report_prompt

    settings = get_settings()
    prompt = build_reasoning_report_prompt(lead_json, score, score_breakdown, bant_json)
    messages = [{"role": "user", "content": prompt}]

    raw = await _call_local_llm(
        messages,
        timeout=float(settings.local_llm_timeout_reasoning),
        max_tokens=1500,
    )
    json_str = _extract_json(raw)

    try:
        return _reasoning_adapter.validate_json(json_str)
    except (ValidationError, json.JSONDecodeError) as exc:
        log.warning("reasoning_validation_failed", error=str(exc), raw=raw[:200])
        raise
