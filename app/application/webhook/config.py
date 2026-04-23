"""
Webhook subsystem constants + Redis key naming.

Retry schedule chosen for score.updated events:
    [0, 30s, 2m, 10m, 1h, 6h]  — 6 attempts, ~7h15m total.
Rationale:
    - 30s covers transient blips (consumer restart, GC pause).
    - 2m + 10m cover typical redeploy windows.
    - 1h + 6h are the long-tail for extended incidents; beyond that a
      score event is stale anyway (the lead has either progressed or
      the consumer can backfill via `/v1/leads/{id}/score`).
"""

from __future__ import annotations

# Attempts delays are RELATIVE to the previous attempt's failure.
# Index 0 is the initial attempt (immediate).
RETRY_DELAYS_SECONDS: tuple[int, ...] = (0, 30, 120, 600, 3600, 21600)

MAX_ATTEMPTS: int = len(RETRY_DELAYS_SECONDS)

# HTTP connect + read timeout for a single delivery attempt.
DELIVERY_TIMEOUT_SECONDS: float = 10.0

# Worker loop blocking pops — responsiveness vs idle churn.
BLPOP_TIMEOUT_SECONDS: int = 5

# Retry scheduler poll interval.
RETRY_POLL_SECONDS: float = 1.0

# DLQ retention per tenant (hard cap, oldest LPOP'd when exceeded).
DLQ_MAX_ENTRIES: int = 500

# Tenant webhook config cache TTL. `/config` PATCH invalidates via
# delete on this key; a short TTL bounds staleness if bypass happens.
TENANT_CONFIG_CACHE_TTL_SECONDS: int = 120

# Signature timestamp replay window (consumer-side suggestion).
SIGNATURE_TIMESTAMP_WINDOW_SECONDS: int = 300


# ─── Redis key naming ────────────────────────────────────────────────────────
#
# Per-tenant FIFO of ready-to-deliver jobs. Worker BLPOPs across a list of
# known tenant keys (rebuilt periodically from the tenant table). For Phase
# 2.M scale we use a single shared queue key — per-tenant fairness can be
# revisited once multi-tenant webhook volume is non-trivial.


def queue_key() -> str:
    """Shared FIFO of delivery jobs. Each job's payload carries tenant_id."""
    return "webhook:queue"


def retry_key() -> str:
    """Global ZSET — score = next_attempt_unix_ms, member = JSON job."""
    return "webhook:retry"


def dlq_key(tenant_id: str) -> str:
    """Per-tenant DLQ (LIST of JSON jobs, newest at head)."""
    return f"webhook:dlq:{tenant_id}"


def inflight_key(tenant_id: str, event_id: str) -> str:
    """Short-TTL marker to dedupe retries across worker restarts."""
    return f"webhook:inflight:{tenant_id}:{event_id}"


def tenant_config_cache_key(tenant_id: str) -> str:
    """Cached `(url, secret)` tuple; JSON-encoded."""
    return f"webhook:tenant_cfg:{tenant_id}"
