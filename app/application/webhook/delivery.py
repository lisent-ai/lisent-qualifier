"""
Single-attempt webhook delivery.

Called by `WebhookWorker` — kept isolated so retry/DLQ policy lives in the
worker and this function only concerns itself with "POST one payload and
report what happened."
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.application.webhook.config import DELIVERY_TIMEOUT_SECONDS
from app.application.webhook.signing import sign_payload
from app.metrics import WEBHOOK_DELIVERY_LATENCY

log = structlog.get_logger(__name__)


@dataclass
class DeliveryOutcome:
    ok: bool
    status_code: int | None  # None if transport error
    error: str | None        # filled when ok=False and status is unhelpful
    latency_seconds: float


async def deliver(
    *,
    url: str,
    secret: str,
    body: bytes,
    event_id: str,
    event_type: str,
    delivery_id: str,
    tenant_id: str,
    timestamp_ms: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> DeliveryOutcome:
    """POST `body` to `url` with HMAC-signed Lisent headers.

    `client` is optional — tests inject a MockTransport-backed client.
    In production the worker owns a single long-lived httpx.AsyncClient
    for connection reuse.
    """
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    signature = sign_payload(secret, ts, body)

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Lisent-Qualifier-Webhook/1.0",
        "X-Lisent-Signature": signature,
        "X-Lisent-Timestamp": str(ts),
        "X-Lisent-Event-Id": event_id,
        "X-Lisent-Event-Type": event_type,
        "X-Lisent-Delivery-Id": delivery_id,
    }

    # Loop-prevention metadata. When the event body carries origin_system +
    # external_id (set by the publisher for leads that originated in a partner
    # CRM), surface them as headers so the receiver can upsert by external_id
    # and suppress echo-back without parsing the body. Body always wins; the
    # headers are a fast-path hint.
    origin, external_id = _extract_origin_headers(body)
    if origin:
        headers["X-Lisent-Origin"] = origin
    if external_id:
        headers["X-Lisent-External-Id"] = external_id

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_SECONDS, http2=True)

    started = time.monotonic()
    try:
        try:
            response = await client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            latency = time.monotonic() - started
            WEBHOOK_DELIVERY_LATENCY.labels(tenant_id=tenant_id).observe(latency)
            log.warning(
                "webhook_delivery_transport_error",
                tenant_id=tenant_id,
                event_id=event_id,
                delivery_id=delivery_id,
                error=type(exc).__name__,
                detail=str(exc),
                latency_ms=int(latency * 1000),
            )
            return DeliveryOutcome(
                ok=False,
                status_code=None,
                error=f"{type(exc).__name__}: {exc}",
                latency_seconds=latency,
            )

        latency = time.monotonic() - started
        WEBHOOK_DELIVERY_LATENCY.labels(tenant_id=tenant_id).observe(latency)

        ok = 200 <= response.status_code < 300
        event: dict[str, Any] = {
            "tenant_id": tenant_id,
            "event_id": event_id,
            "delivery_id": delivery_id,
            "status_code": response.status_code,
            "latency_ms": int(latency * 1000),
        }
        if ok:
            log.info("webhook_delivery_success", **event)
        else:
            log.warning(
                "webhook_delivery_rejected",
                **event,
                response_body=response.text[:500],
            )
        return DeliveryOutcome(
            ok=ok,
            status_code=response.status_code,
            error=None if ok else f"http_{response.status_code}",
            latency_seconds=latency,
        )
    finally:
        if owns_client:
            await client.aclose()


def _extract_origin_headers(body: bytes) -> tuple[str | None, str | None]:
    """Pull origin_system + external_id out of the serialized event body.

    Looks at top-level keys first, then `payload.*` — publishers may put the
    metadata either place. Returns (origin_system, external_id) with Nones
    when absent. Defensive against malformed JSON: webhook signing already
    happened, so a parse error here must never break delivery.
    """
    try:
        doc = json.loads(body)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(doc, dict):
        return None, None

    def _pick(key: str) -> str | None:
        val = doc.get(key)
        if val is None and isinstance(doc.get("payload"), dict):
            val = doc["payload"].get(key)
        if val is None:
            return None
        s = str(val).strip()
        return s or None

    return _pick("origin_system"), _pick("external_id")
