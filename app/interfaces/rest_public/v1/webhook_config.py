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
import uuid
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl

from app.application.webhook.config import dlq_key
from app.application.webhook.event_filter import validate_patterns
from app.application.webhook.worker import invalidate_tenant_config_cache
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.redis.client import get_redis
from app.interfaces.rest_public.auth import get_current_tenant
from app.interfaces.rest_public.di import get_event_port
from app.ports.event import EVENT_CATALOG, EVENT_TYPES, EventPort, ScoreEvent
from app.ports.tenant import Tenant

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/config/webhook", tags=["v1-config-webhook"])


# ─────────────────────────────────────────────────────────────────── schemas


class WebhookConfigResponse(BaseModel):
    url: str | None
    has_secret: bool
    secret_rotated_at: datetime | None = None
    enabled_events: list[str] = Field(default_factory=lambda: ["*"])
    payload_mode: str = "full"
    event_catalog: dict[str, list[str]] = Field(default_factory=dict)
    dlq_size: int
    recent_dlq: list[dict[str, Any]] = Field(default_factory=list)


class WebhookPatchRequest(BaseModel):
    url: HttpUrl | None = None
    rotate_secret: bool = False
    enabled_events: list[str] | None = None
    payload_mode: str | None = Field(
        default=None,
        description="'full' (default, embed event payload) or 'minimal' (strip payload — consumer pulls via REST).",
    )


class WebhookPatchResponse(BaseModel):
    url: str | None
    has_secret: bool
    secret: str | None = Field(
        default=None,
        description="Plaintext — returned ONCE on rotation or initial set. Persist client-side immediately.",
    )
    secret_rotated_at: datetime | None = None
    enabled_events: list[str] = Field(default_factory=lambda: ["*"])
    payload_mode: str = "full"


class WebhookTestRequest(BaseModel):
    lead_id: UUID | None = None  # defaults to a fresh uuid4
    score: int = Field(default=77, ge=0, le=100)
    event_type: str = Field(
        default="lead.qualified",
        description="Pipeline event to fire. Must be one of the outbound-allowlisted types.",
    )
    external_id: str | None = Field(
        default=None,
        description="Synthetic partner external_id. Defaults to 'test-<uuid>' if omitted.",
    )
    from_stage: str = "contacted"
    to_stage: str = "qualified"
    origin_system: str = "lisent"
    source: str = "lisent_native"


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


async def _fetch_filter_config(tenant_id: UUID) -> tuple[list[str], str]:
    """Return `(enabled_events, payload_mode)` with defaults on miss."""
    pool = get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT outbound_webhook_enabled_events, outbound_webhook_payload_mode
            FROM tenants WHERE id = $1
            """,
            tenant_id,
        )
    if not row:
        return ["*"], "full"
    return list(row["outbound_webhook_enabled_events"] or ["*"]), (
        row["outbound_webhook_payload_mode"] or "full"
    )


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
    enabled, payload_mode = await _fetch_filter_config(tenant.id)
    size, recent = await _read_dlq(tenant.id, limit=10)
    return WebhookConfigResponse(
        url=url,
        has_secret=bool(secret),
        secret_rotated_at=rotated_at,
        enabled_events=enabled,
        payload_mode=payload_mode,
        event_catalog=EVENT_CATALOG,
        dlq_size=size,
        recent_dlq=recent,
    )


@router.patch("", response_model=WebhookPatchResponse)
async def patch_webhook_config(
    body: WebhookPatchRequest,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> WebhookPatchResponse:
    if (
        body.url is None
        and not body.rotate_secret
        and body.enabled_events is None
        and body.payload_mode is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="supply at least one of: url, rotate_secret, enabled_events, payload_mode",
        )

    if body.payload_mode is not None and body.payload_mode not in ("full", "minimal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload_mode must be 'full' or 'minimal'",
        )

    if body.enabled_events is not None:
        if len(body.enabled_events) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="enabled_events cannot be empty — use ['*'] to allow all",
            )
        bad = validate_patterns(body.enabled_events, EVENT_TYPES)
        if bad:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown event pattern(s): {bad}",
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

    if body.enabled_events is not None:
        params.append(body.enabled_events)
        updates.append(f"outbound_webhook_enabled_events = ${len(params)}::text[]")

    if body.payload_mode is not None:
        params.append(body.payload_mode)
        updates.append(f"outbound_webhook_payload_mode = ${len(params)}")

    query = (
        f"UPDATE tenants SET {', '.join(updates)}, updated_at = now() WHERE id = $1 "
        "RETURNING outbound_webhook_url, outbound_webhook_secret, "
        "outbound_webhook_enabled_events, outbound_webhook_payload_mode"
    )
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
        enabled_events=list(row["outbound_webhook_enabled_events"] or ["*"]) if row else ["*"],
        payload_mode=(row["outbound_webhook_payload_mode"] if row else None) or "full",
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

    if body.event_type not in EVENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown event_type: {body.event_type}",
        )

    lead_id = body.lead_id or uuid.uuid4()
    external_id = (body.external_id or f"test-{lead_id}").strip()
    ts = datetime.utcnow()
    # Synthetic but realistically-shaped CRM lead snapshot so the fanout
    # adapter can render a 'full' payload identical to a real stage change.
    test_lead = {
        "id": str(lead_id),
        "name": "Test Lead",
        "email": "test@example.com",
        "phone": "+10000000000",
        "status": body.to_stage,
        "source": body.source,
        "value": 0,
        "external_id": external_id,
        "origin_system": body.origin_system,
    }
    event = ScoreEvent(
        tenant_id=tenant.id,
        lead_id=lead_id,
        session_id=None,
        event_type=body.event_type,
        score=body.score,
        threshold=75,
        path="crm",
        payload={
            "test": True,
            "from_stage": body.from_stage,
            "to_stage": body.to_stage,
            "changed_by": "webhook_test",
            "changed_at": ts.isoformat(),
            "lead": test_lead,
        },
        timestamp=ts,
        external_id=external_id,
        origin_system=body.origin_system,
        source=body.source,
    )
    await event_port.publish(event)

    event_id = f"{external_id}.{body.event_type}.{int(ts.timestamp() * 1000)}"
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
