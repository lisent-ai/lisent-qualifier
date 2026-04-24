"""
Lead CRUD endpoints — POST / GET /v1/leads (+ /{id}).

Phase 2.B (current): Basic CRUD — lead create/read (scoring yok).
Phase 2.C: Glass Box CHAMP response (score + evidence + confidence).
Phase 2.D: SSE score streaming.

External tenants bu endpoint'leri API key auth ile kullanır. Tenant_id
current_tenant'tan otomatik atanır (body'de tenant_id ACCEPT EDİLMEZ —
hack önlenir). RLS zaten DB seviyesinde isolation sağlar.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.infrastructure.db.pool import get_db_pool
from app.interfaces.rest_public.rate_limit_dep import enforce_rate_limit
from app.ports.tenant import Tenant

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/leads", tags=["v1-leads"])


# ============================================================================
# Schemas
# ============================================================================


class LeadCreateRequest(BaseModel):
    """POST /v1/leads body — external lead ingest."""

    external_ref: str | None = Field(
        None,
        description="Upstream ID (e.g. CRM lead_id). Omit → auto UUID. Used for idempotency.",
    )
    name: str | None = Field(None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(None, max_length=50)
    city: str | None = Field(None, max_length=255)
    source: str | None = Field(None, max_length=100)
    project_type: str | None = Field(None, max_length=50)
    budget_range: str | None = Field(None, max_length=50)
    notes: str | None = None
    extra_data: dict[str, Any] | None = None


class LeadResponse(BaseModel):
    """GET /v1/leads/{id} + POST response."""

    id: UUID
    tenant_id: UUID
    external_ref: str
    name: str | None
    email: str | None
    phone: str | None
    city: str | None
    source: str | None
    project_type: str | None
    budget_range: str | None
    notes: str | None
    score: int
    status: str
    path: str | None
    extra_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class LeadListResponse(BaseModel):
    """GET /v1/leads list response with cursor pagination (Phase 2.P)."""

    data: list[LeadResponse]
    pagination: dict[str, Any]


# ============================================================================
# Helpers
# ============================================================================


def _row_to_lead(row: Any, tenant_id: UUID) -> LeadResponse:
    extra_raw = row["extra_data"]
    if isinstance(extra_raw, str):
        try:
            extra = json.loads(extra_raw)
        except json.JSONDecodeError:
            extra = {}
    elif isinstance(extra_raw, dict):
        extra = extra_raw
    else:
        extra = {}

    return LeadResponse(
        id=row["id"],
        tenant_id=tenant_id,
        external_ref=row["lead_id"],
        name=row.get("name"),
        email=row.get("email"),
        phone=row.get("phone") or None,
        city=row.get("city"),
        source=row.get("source"),
        project_type=row.get("project_type"),
        budget_range=row.get("budget_range"),
        notes=extra.get("notes"),
        score=int(row.get("score") or 0),
        status=row.get("status") or "new",
        path=row.get("path"),
        extra_data=extra,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ============================================================================
# POST /v1/leads
# ============================================================================


@router.post(
    "",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Duplicate external_ref"}},
)
async def create_lead(
    body: LeadCreateRequest,
    tenant: Annotated[Tenant, Depends(enforce_rate_limit)],
) -> LeadResponse:
    """Yeni lead oluştur. Scoring asenkron (Phase 2.C'de eklenir).

    Idempotency: external_ref verilmişse (tenant_id, external_ref) unique — aynı
    ref ile tekrar çağrılırsa 409 döner. Omit edilirse auto UUID (idempotency yok).
    """
    external_ref = body.external_ref or str(uuid.uuid4())
    extra = dict(body.extra_data or {})
    if body.notes:
        extra["notes"] = body.notes

    pool = get_db_pool()
    async with pool.acquire() as conn:
        # Tenant isolation + FORCE RLS için transaction içinde SET LOCAL
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant.id}'")

            # Dedup: aynı tenant + external_ref varsa 409
            existing = await conn.fetchrow(
                """
                SELECT id FROM qualifier_leads
                WHERE tenant_id = $1 AND lead_id = $2
                """,
                tenant.id,
                external_ref,
            )
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Lead with external_ref={external_ref!r} already exists",
                )

            # INSERT (company_id = tenant_id for standalone tenants — legacy column compat)
            row = await conn.fetchrow(
                """
                INSERT INTO qualifier_leads (
                    tenant_id, company_id, lead_id, phone, name, email, city,
                    source, project_type, budget_range, extra_data, status
                )
                VALUES ($1, $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, 'new')
                RETURNING *
                """,
                tenant.id,
                external_ref,
                body.phone or "",
                body.name,
                body.email,
                body.city,
                body.source,
                body.project_type,
                body.budget_range,
                json.dumps(extra),
            )

    log.info(
        "v1_lead_created",
        tenant_id=str(tenant.id),
        lead_id=str(row["id"]),
        external_ref=external_ref,
    )

    # Phase 2.G — record usage
    try:
        from app.infrastructure.redis.client import get_redis
        from app.infrastructure.usage.counter import RedisUsageCounter

        await RedisUsageCounter(get_redis()).incr(tenant.id, "leads_ingested")
    except Exception as exc:
        log.warning("usage_counter_failed", error=str(exc))

    # Phase 3 — enqueue pre-scoring job (async; response returns score=0).
    # Worker processes → ensemble + OSINT → UPDATE qualifier_leads.score.
    # Consumers poll GET /v1/leads/{id}/score or subscribe to score-stream.
    # Gated by the same flag that controls the worker — no point enqueuing
    # jobs that won't be picked up (and no point holding a Redis connection
    # open in test fixtures that aren't exercising the pipeline).
    from app.config import get_settings as _get_settings
    if _get_settings().prescore_worker_enabled:
        try:
            from app.application.prescore.queue import enqueue_lead
            from app.infrastructure.redis.client import get_redis

            await enqueue_lead(
                get_redis(),
                tenant_id=tenant.id,
                lead_id=row["id"],
            )
        except Exception as exc:
            log.warning(
                "prescore_enqueue_failed",
                tenant_id=str(tenant.id),
                lead_id=str(row["id"]),
                error=str(exc),
            )
            # Lead is still persisted; manual backfill possible via replay script.

    return _row_to_lead(row, tenant.id)


# ============================================================================
# GET /v1/leads
# ============================================================================


@router.get("", response_model=LeadListResponse)
async def list_leads(
    tenant: Annotated[Tenant, Depends(enforce_rate_limit)],
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, description="Base64-encoded cursor from previous page's next_cursor"),
    status_filter: str | None = Query(default=None, alias="status"),
) -> LeadListResponse:
    """Tenant'ın lead'lerini listele. Cursor-based pagination (Phase 2.P: full implementation)."""
    import base64

    cursor_dt: datetime | None = None
    cursor_id: UUID | None = None
    if cursor:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            cursor_dt = datetime.fromisoformat(decoded["t"])
            cursor_id = UUID(decoded["id"])
        except Exception:
            raise HTTPException(400, "Invalid cursor")

    pool = get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant.id}'")

            where = ["tenant_id = $1"]
            params: list[Any] = [tenant.id]
            if status_filter:
                params.append(status_filter)
                where.append(f"status = ${len(params)}")
            if cursor_dt is not None and cursor_id is not None:
                params.extend([cursor_dt, cursor_id])
                where.append(
                    f"(created_at, id) < (${len(params)-1}::timestamptz, ${len(params)}::uuid)"
                )

            # Fetch limit+1 to detect has_more
            params.append(limit + 1)
            query = f"""
                SELECT * FROM qualifier_leads
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC, id DESC
                LIMIT ${len(params)}
            """
            rows = await conn.fetch(query, *params)

    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        token = json.dumps({"t": last["created_at"].isoformat(), "id": str(last["id"])})
        next_cursor = base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")

    return LeadListResponse(
        data=[_row_to_lead(r, tenant.id) for r in rows],
        pagination={
            "has_more": has_more,
            "next_cursor": next_cursor,
            "count": len(rows),
        },
    )


# ============================================================================
# GET /v1/leads/{id}
# ============================================================================


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: UUID,
    tenant: Annotated[Tenant, Depends(enforce_rate_limit)],
) -> LeadResponse:
    """Tek lead detayı — application-level tenant filter (RLS ikincil defense).

    NOT: uygulama DB'ye `app` superuser ile bağlandığı için RLS bypass edilir.
    Tenant isolation application-level `AND tenant_id = $2` ile enforce edilir.
    Phase 2+: DATABASE_URL `app_runtime` user'ına switch edilince RLS native
    enforce edecek — bu filter yine defense-in-depth olarak kalır.
    """
    pool = get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant.id}'")
            row = await conn.fetchrow(
                "SELECT * FROM qualifier_leads WHERE id = $1 AND tenant_id = $2",
                lead_id,
                tenant.id,
            )
    if row is None:
        raise HTTPException(404, "Lead not found")
    return _row_to_lead(row, tenant.id)
