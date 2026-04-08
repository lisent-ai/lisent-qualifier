"""
Groq streaming client using openai/gpt-oss-120b.
Circuit breaker + async generator for SSE streaming.
"""
import structlog
from typing import AsyncGenerator

from circuitbreaker import circuit
from groq import AsyncGroq, APIStatusError, APITimeoutError

from app.config import get_settings

log = structlog.get_logger(__name__)

_groq_client: AsyncGroq | None = None


def get_groq_client() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        settings = get_settings()
        _groq_client = AsyncGroq(
            api_key=settings.groq_api_key,
            timeout=settings.groq_timeout_seconds,
        )
    return _groq_client


async def close_groq_client() -> None:
    global _groq_client
    if _groq_client is not None:
        await _groq_client.close()
        _groq_client = None


@circuit(failure_threshold=3, recovery_timeout=60, expected_exception=(APIStatusError, APITimeoutError, Exception))
async def stream_chat(
    messages: list[dict],
    session_id: str,
) -> AsyncGenerator[str, None]:
    """Yields token strings. Raises on circuit open or Groq error."""
    from app.infrastructure.llm.groq_rate_limiter import acquire
    if not await acquire(estimated_tokens=4000, priority="chat"):
        raise RuntimeError("Groq rate limit — chat throttled")

    settings = get_settings()
    client = get_groq_client()

    stream = await client.chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        max_tokens=settings.groq_max_tokens,
        temperature=0.7,
        stream=True,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


async def complete_chat(messages: list[dict]) -> str:
    """Non-streaming completion for handoff closing message."""
    from app.infrastructure.llm.groq_rate_limiter import acquire
    if not await acquire(estimated_tokens=500, priority="other"):
        return ""  # Skip closing message if rate limited

    settings = get_settings()
    client = get_groq_client()

    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        max_tokens=256,
        temperature=0.5,
        stream=False,
    )
    return response.choices[0].message.content or ""
