"""Sliding-window rate limiter backed by Redis sorted sets."""

import time

from app.infrastructure.redis.client import get_redis

# Key pattern: rag_ratelimit:{token}
_KEY_PREFIX = "rag_ratelimit"
_WINDOW_SECONDS = 1
_DEFAULT_LIMIT = 20
_DEFAULT_BURST = 50


async def check_rate_limit(
    token: str,
    *,
    limit: int = _DEFAULT_LIMIT,
    burst: int = _DEFAULT_BURST,
) -> tuple[bool, int]:
    """Check whether the token is within its rate limit.

    Returns (allowed, retry_after_seconds).
    ``retry_after_seconds`` is 0 when allowed.
    """
    redis = get_redis()
    key = f"{_KEY_PREFIX}:{token}"
    now = time.time()
    window_start = now - _WINDOW_SECONDS

    pipe = redis.pipeline(transaction=True)
    # Remove entries outside the window
    pipe.zremrangebyscore(key, "-inf", window_start)
    # Count entries in the window
    pipe.zcard(key)
    # Add current request
    pipe.zadd(key, {f"{now}": now})
    # Set expiry so keys don't linger forever
    pipe.expire(key, _WINDOW_SECONDS + 1)
    results = await pipe.execute()

    count = results[1]  # zcard result (before adding current)

    if count >= burst:
        # Over burst — hard reject
        return False, _WINDOW_SECONDS

    if count >= limit:
        # Over sustained limit — soft reject
        return False, _WINDOW_SECONDS

    return True, 0
