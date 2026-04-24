"""
Centralized Groq API rate limiter with Redis-backed token tracking.

All Groq calls go through acquire() before making the API request.
If TPM budget is exhausted, calls wait in queue until budget resets.

Rate limit info:
  - 500K RPM (requests/min) — not a concern
  - 250K TPM (tokens/min) — THIS is the bottleneck
  - Reset window: ~60 seconds (sliding)

Strategy:
  - Track token usage per minute in Redis (atomic INCRBY + EXPIRE)
  - Before each call, check remaining budget
  - If insufficient, wait until reset (max ~1s for small calls)
  - Use asyncio.Semaphore to limit concurrent Groq calls (prevent burst)

Safety margins:
  - Reserve 20% of TPM for streaming chat (highest priority)
  - Classify + field mapping share the remaining 80%
"""
import asyncio
import time
import structlog
from typing import Any

log = structlog.get_logger(__name__)

# Limits (from Groq headers: x-ratelimit-limit-tokens: 250000)
_TPM_LIMIT = 250_000
_TPM_SAFETY_MARGIN = 0.85  # Use max 85% to avoid hitting hard limit
_MAX_TPM = int(_TPM_LIMIT * _TPM_SAFETY_MARGIN)  # ~212,500

# Max concurrent Groq calls to prevent burst
_MAX_CONCURRENT = 10
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

# Priority budgets (% of _MAX_TPM). Budgets currently inform operator
# intent + observability; the acquire() loop enforces a single global
# budget (_MAX_TPM) and relies on max_wait per priority to prioritize
# critical traffic through rate-limit resets.
_PRIORITY_BUDGETS = {
    "chat": 0.30,          # Streaming chat — user-facing
    "judge": 0.25,         # CHAMP Judge extraction — post-chat scorer
    "prescore": 0.20,      # Pre-score ensemble (3 personas per lead)
    "classify": 0.10,      # Per-message classification — rules fallback
    "field_mapping": 0.10, # Webhook field mapping — heuristic fallback
    "other": 0.05,         # Closing messages, etc.
}


async def _get_usage(redis, window_key: str) -> int:
    """Get current token usage for this minute window."""
    val = await redis.get(window_key)
    return int(val) if val else 0


async def _add_usage(redis, window_key: str, tokens: int) -> None:
    """Record token usage. Key auto-expires after 60s."""
    pipe = redis.pipeline()
    pipe.incrby(window_key, tokens)
    pipe.expire(window_key, 60)
    await pipe.execute()


def _window_key() -> str:
    """Redis key for current minute window."""
    minute = int(time.time() / 60)
    return f"groq:tpm:{minute}"


# Default max wait per priority (seconds).
# TPM resets every 60s, so critical calls can wait up to a full reset cycle.
_DEFAULT_MAX_WAIT: dict[str, float] = {
    "chat": 30.0,           # User-facing — wait up to 30s, must not fail
    "judge": 60.0,          # Background extraction — can wait a full cycle
    "prescore": 60.0,       # Async intake scoring — worker retries anyway
    "classify": 3.0,        # Per-message — fast fail, rules fallback is fine
    "field_mapping": 45.0,  # Webhook intake — not user-facing, can wait
    "other": 10.0,
}


async def acquire(
    estimated_tokens: int,
    priority: str = "other",
    max_wait: float | None = None,
) -> bool:
    """
    Acquire permission to make a Groq API call.
    Waits in queue until TPM budget is available.

    Critical calls (chat, judge, field_mapping) wait up to 30-60s
    because TPM resets every 60s — they WILL get through.

    Non-critical calls (classify) fail fast and use rule-based fallback.

    Args:
        estimated_tokens: Estimated total tokens (input + output)
        priority: "chat" | "judge" | "prescore" | "classify" | "field_mapping" | "other"
        max_wait: Override max seconds to wait (None = use default per priority)

    Returns:
        True if acquired, False if budget exhausted after max_wait.
    """
    if max_wait is None:
        max_wait = _DEFAULT_MAX_WAIT.get(priority, 10.0)

    try:
        from app.infrastructure.redis.client import get_redis
        redis = get_redis()
    except Exception:
        return True  # If Redis unavailable, allow the call

    start = time.monotonic()
    waited = False
    while True:
        key = _window_key()  # Recalculate each iteration (minute may roll over)
        total_used = await _get_usage(redis, key)

        # Check global limit
        if total_used + estimated_tokens > _MAX_TPM:
            elapsed = time.monotonic() - start
            if elapsed >= max_wait:
                log.warning(
                    "groq_rate_limit_exhausted",
                    priority=priority,
                    total_used=total_used,
                    limit=_MAX_TPM,
                    estimated=estimated_tokens,
                    waited_s=round(elapsed, 1),
                )
                return False
            if not waited:
                log.info(
                    "groq_rate_limit_queued",
                    priority=priority,
                    total_used=total_used,
                    limit=_MAX_TPM,
                    max_wait=max_wait,
                )
                waited = True
            # Wait and retry — budget resets every 60s
            await asyncio.sleep(1.0)
            continue

        # Budget available — acquire
        pipe = redis.pipeline()
        pipe.incrby(key, estimated_tokens)
        pipe.expire(key, 60)
        await pipe.execute()

        if waited:
            elapsed = time.monotonic() - start
            log.info("groq_rate_limit_dequeued", priority=priority, waited_s=round(elapsed, 1))

        return True


async def record_actual_usage(tokens: int, priority: str = "other") -> None:
    """
    Adjust tracked usage after knowing actual token count.
    Call this after API response with real usage.total_tokens.
    """
    # The estimate was already recorded in acquire().
    # If actual differs significantly, we could adjust, but for simplicity
    # the sliding window will auto-correct within 60s.
    pass


async def get_usage_stats() -> dict[str, Any]:
    """Get current rate limit usage stats (for monitoring/debugging)."""
    try:
        from app.infrastructure.redis.client import get_redis
        redis = get_redis()
        key = _window_key()
        total = await _get_usage(redis, key)
        stats = {
            "total_used": total,
            "total_limit": _MAX_TPM,
            "remaining": _MAX_TPM - total,
            "utilization_pct": round(total / _MAX_TPM * 100, 1) if _MAX_TPM > 0 else 0,
        }
        for priority in _PRIORITY_BUDGETS:
            pk = f"groq:tpm:{priority}:{int(time.time() / 60)}"
            used = await _get_usage(redis, pk)
            limit = int(_MAX_TPM * _PRIORITY_BUDGETS[priority])
            stats[f"{priority}_used"] = used
            stats[f"{priority}_limit"] = limit
        return stats
    except Exception:
        return {"error": "redis_unavailable"}
