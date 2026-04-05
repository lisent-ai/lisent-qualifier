"""
httpx async client for local llama.cpp server.
Circuit breaker + tenacity retry + structured output via response_format.
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
from .schemas import (
    CHAMPExtractionResult,
    CHAMPFullExtractionResult,
    ReasoningReportResult,
    SectorQualifiersResult,
    MessageAnalysis,
)

log = structlog.get_logger(__name__)

_champ_adapter = TypeAdapter(CHAMPExtractionResult)
_full_adapter = TypeAdapter(CHAMPFullExtractionResult)
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
    """Extract first JSON object from LLM output (handles markdown code blocks).
    Fallback for when response_format is not supported by the server."""
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
async def _call_local_llm(
    messages: list[dict],
    timeout: float,
    max_tokens: int = 1024,
    response_format: dict | None = None,
) -> str:
    """Raw LLM call with circuit breaker and optional structured output."""
    client = get_http_client()
    settings = get_settings()
    payload: dict[str, Any] = {
        "model": settings.local_llm_model,
        "messages": messages,
        "temperature": 0.05,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if response_format:
        payload["response_format"] = response_format

    response = await client.post(
        "/v1/chat/completions",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def _build_champ_response_format() -> dict:
    """Build response_format for CHAMP extraction using JSON schema."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "champ_extraction",
            "strict": True,
            "schema": CHAMPExtractionResult.model_json_schema(),
        },
    }


def _build_full_extraction_response_format() -> dict:
    """Build response_format for full extraction (CHAMP + sector + message analysis)."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "champ_full_extraction",
            "strict": True,
            "schema": CHAMPFullExtractionResult.model_json_schema(),
        },
    }


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((httpx.HTTPError, ValidationError)),
    reraise=True,
)
async def extract_champ(conversation_text: str, prompt: str | None = None) -> CHAMPExtractionResult:
    """Extract CHAMP scores from conversation using local LLM.

    Uses structured output (response_format) when available,
    falls back to regex JSON extraction otherwise.
    """
    if prompt is None:
        from app.domain.conversation.prompts import build_champ_extraction_prompt
        prompt = build_champ_extraction_prompt(conversation_text)

    settings = get_settings()
    messages = [{"role": "user", "content": prompt}]

    try:
        raw = await _call_local_llm(
            messages,
            timeout=float(settings.local_llm_timeout_champ),
            response_format=_build_champ_response_format(),
        )
        return _champ_adapter.validate_json(raw)
    except (httpx.HTTPError, ValidationError):
        # Retry with regex fallback (response_format might not be supported)
        raw = await _call_local_llm(
            messages,
            timeout=float(settings.local_llm_timeout_champ),
        )
        json_str = _extract_json(raw)
        try:
            return _champ_adapter.validate_json(json_str)
        except (ValidationError, json.JSONDecodeError) as exc:
            log.warning("champ_validation_failed", error=str(exc), raw=raw[:200])
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
    champ_json: dict[str, Any] | None = None,
    company_config: dict[str, Any] | None = None,
) -> ReasoningReportResult:
    from app.domain.conversation.prompts import build_reasoning_report_prompt

    settings = get_settings()
    prompt = build_reasoning_report_prompt(
        lead_json, score, score_breakdown, champ_json, company_config
    )
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
