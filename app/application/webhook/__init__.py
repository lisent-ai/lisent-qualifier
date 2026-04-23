"""
Outbound webhook subsystem (Phase 2.M).

Responsibility split:
    - `signing`     — HMAC-SHA256 helpers (compute + verify); no I/O.
    - `config`      — retry schedule, timeouts, Redis key naming.
    - `delivery`    — single POST attempt with signed headers + metrics.
    - `worker`      — asyncio dispatch loop + retry scheduler + DLQ sink,
                      started/stopped by the FastAPI lifespan.

Publishers stay untouched — `WebhookFanoutAdapter` (in `app.adapters.event`)
enqueues jobs, this package consumes them.
"""

from app.application.webhook.config import (
    DELIVERY_TIMEOUT_SECONDS,
    MAX_ATTEMPTS,
    RETRY_DELAYS_SECONDS,
    dlq_key,
    inflight_key,
    queue_key,
    retry_key,
)
from app.application.webhook.delivery import DeliveryOutcome, deliver
from app.application.webhook.signing import sign_payload, verify_signature
from app.application.webhook.worker import WebhookWorker

__all__ = [
    "DELIVERY_TIMEOUT_SECONDS",
    "MAX_ATTEMPTS",
    "RETRY_DELAYS_SECONDS",
    "DeliveryOutcome",
    "WebhookWorker",
    "deliver",
    "dlq_key",
    "inflight_key",
    "queue_key",
    "retry_key",
    "sign_payload",
    "verify_signature",
]
