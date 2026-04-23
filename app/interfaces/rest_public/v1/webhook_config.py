"""
Outbound webhook admin endpoints (Phase 2.M).

Paths (under the v1 prefix):
    GET    /v1/config/webhook              — current config + recent stats
    PATCH  /v1/config/webhook              — set url, optionally rotate secret
    POST   /v1/config/webhook/test         — publish a synthetic score event
    GET    /v1/config/webhook/deliveries   — recent DLQ entries (last N)

Secret model:
    - Stored plaintext in `tenants.outbound_webhook_secret` (column-level
      access is already restricted to qualifier's app role + platform).
    - Returned to the caller ONLY once — on initial set or explicit
      rotate. Subsequent GETs return `has_secret: true` with a rotated_at
      timestamp but never the value.

All endpoints are tenant-scoped via `get_current_tenant` (same bearer/
source_ref auth as the rest of v1).
"""

from __future__ import annotations

import json
import secrets as _secrets
import time
import uuid
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl

from app.application.webhook.config import dlq_key
from app.application.webhook.worker import invalidate_tenant_config_cache
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.redis.client import get_redis
from app.interfaces.rest_public.auth import get_current_tenant
from app.interfaces.rest_public.di import get_event_port
from app.ports.event import EventPort, ScoreEvent
from app.ports.tenant import Tenant

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/config/webhook", tags=["v1-config-webhook"])


# ─────────────────────────────────────────────────────────────────── schemas


class WebhookConfigResponse(BaseModel):
    url: str | None
    has_secret: bool
    secret_rotated_at: datetime | None = None
    dlq_size: int
    recent_dlq: list[dict[str, Any]] = Field(default_factory=list)


class WebhookPatchRequest(BaseModel):
    url: HttpUrl | None = None
    rotate_secret: bool = False


class WebhookPatchResponse(BaseModel):
    url: str | None
    has_secret: bool
    secret: str | None = Field(
        default=None,
        description="Plaintext — returned ONCE on rotation or initial set. Persist client-side immediately.",
    )
    secret_rotated_at: datetime | None = None


class WebhookTestRequest(BaseModel):
    lead_id: UUID | None = None  # defaults to a throwaway zero uuid
    score: int = Field(default=42, ge=0, le=100)


class WebhookTestResponse(BaseModel):
    enqueued: bool
    tenant_id: UUID
    event_id: str
    url: str | None


# ─────────────────────────────────────────────────────────────────── helpers


async def _fetch_secret_meta(tenant_id: UUID) -> tuple[str | None, str | None, datetime | None]:
    """Return `(url, secret, secret_rotated_at)` from the DB."""
    pool = get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT outbound_webhook_url, outbound_webhook_secret,
                   (config->>'outbound_webhook_secret_rotated_at')::timestamptz AS rotated_at
            FROM tenants WHERE id = $1
            """,
            tenant_id,
        )
    if not row:
        return None, None, None
    return row["outbound_webhook_url"], row["outbound_webhook_secret"], row["rotated_at"]


async def _read_dlq(tenant_id: UUID, limit: int = 10) -> tuple[int, list[dict[str, Any]]]:
    r = get_redis()
    key = dlq_key(str(tenant_id))
    size = await r.llen(key)
    raw_entries = await r.lrange(key, 0, max(0, limit - 1))
    entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            doc = json.loads(raw)
            # Redact body; keep only the stuff an operator cares about.
            entries.append(
                {
                    "event_id": doc.get("event_id"),
                    "event_type": doc.get("event_type"),
                    "attempt": doc.get("attempt"),
                    "delivery_id": doc.get("delivery_id"),
                    "dlq_reason": doc.get("dlq_reason"),
                    "dlq_at_ms": doc.get("dlq_at_ms"),
                }
            )
        except json.JSONDecodeError:
            continue
    return size, entries


# ─────────────────────────────────────────────────────────────── endpoints


@router.get("", response_model=WebhookConfigResponse)
async def get_webhook_config(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> WebhookConfigResponse:
    url, secret, rotated_at = await _fetch_secret_meta(tenant.id)
    size, recent = await _read_dlq(tenant.id, limit=10)
    return WebhookConfigResponse(
        url=url,
        has_secret=bool(secret),
        secret_rotated_at=rotated_at,
        dlq_size=size,
        recent_dlq=recent,
    )


@router.patch("", response_model=WebhookPatchResponse)
async def patch_webhook_config(
    body: WebhookPatchRequest,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> WebhookPatchResponse:
    if body.url is None and not body.rotate_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="supply url and/or rotate_secret=true",
        )

    new_secret_plain: str | None = None
    rotated_at: datetime | None = None

    pool = get_db_pool()
    updates: list[str] = []
    params: list[Any] = [tenant.id]

    if body.url is not None:
        params.append(str(body.url))
        updates.append(f"outbound_webhook_url = ${len(params)}")
        # Auto-generate a secret on first URL set if none exists yet, so the
        # operator never ships a URL without signing.
        _, existing_secret, _ = await _fetch_secret_meta(tenant.id)
        if existing_secret is None and not body.rotate_secret:
            body = WebhookPatchRequest(url=body.url, rotate_secret=True)

    if body.rotate_secret:
        new_secret_plain = _secrets.token_urlsafe(32)
        params.append(new_secret_plain)
        updates.append(f"outbound_webhook_secret = ${len(params)}")
        rotated_at = datetime.utcnow()
        # Stash rotation timestamp in tenant config JSONB for audit.
        params.append(json.dumps({"outbound_webhook_secret_rotated_at": rotated_at.isoformat() + "+00:00"}))
        updates.append(f"config = config || ${len(params)}::jsonb")

    query = f"UPDATE tenants SET {', '.join(updates)}, updated_at = now() WHERE id = $1 RETURNING outbound_webhook_url, outbound_webhook_secret"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *params)

    await invalidate_tenant_config_cache(get_redis(), str(tenant.id))

    log.info(
        "webhook_config_updated",
        tenant_id=str(tenant.id),
        url_changed=body.url is not None,
        secret_rotated=body.rotate_secret,
    )

    return WebhookPatchResponse(
        url=row["outbound_webhook_url"] if row else None,
        has_secret=bool(row["outbound_webhook_secret"]) if row else False,
        secret=new_secret_plain,
        secret_rotated_at=rotated_at,
    )


@router.post("/test", response_model=WebhookTestResponse)
async def test_webhook(
    body: WebhookTestRequest,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    event_port: Annotated[EventPort, Depends(get_event_port)],
) -> WebhookTestResponse:
    """Publish a synthetic `score.updated` event.

    Behaviour:
        - If tenant has webhook URL configured → job hits `webhook:queue`
          and the worker delivers it asynchronously (consumer should see
          the POST within ~1s).
        - If not configured → event still publishes to SSE (legal no-op)
          but the response flags `enqueued=false`.

    The event payload has `payload.test = true` so consumers can distinguish
    synthetic traffic from real scoring events.
    """
    url, secret, _ = await _fetch_secret_meta(tenant.id)
    if not url or not secret:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webhook url or secret not configured — PATCH /v1/config/webhook first",
        )

    lead_id = body.lead_id or uuid.uuid4()
    ts = datetime.utcnow()
    event = ScoreEvent(
        tenant_id=tenant.id,
        lead_id=lead_id,
        session_id=None,
        event_type="score.updated",
        score=body.score,
        threshold=75,
        path="chat",
        payload={"test": True, "score": body.score},
        timestamp=ts,
    )
    await event_port.publish(event)

    event_id = str(int(ts.timestamp() * 1000))
    log.info(
        "webhook_test_enqueued",
        tenant_id=str(tenant.id),
        event_id=event_id,
        score=body.score,
    )
    return WebhookTestResponse(
        enqueued=True,
        tenant_id=tenant.id,
        event_id=event_id,
        url=url,
    )


@router.get("/deliveries", response_model=list[dict[str, Any]])
async def list_deliveries(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Recent DLQ entries (exhausted-retries deliveries).

    Phase 2.M scope: successful deliveries are only exposed via Prometheus
    (per-tenant `webhook_delivery_attempts_total{status="success"}`). A
    structured success log is Phase 2.M+.
    """
    _, entries = await _read_dlq(tenant.id, limit=max(1, min(limit, 500)))
    return entries
