"""
qualifier_leads tablosuna yazma/okuma işlemleri.
"""
import json
from typing import Any

import asyncpg
import structlog

log = structlog.get_logger(__name__)


async def upsert_lead(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    lead_id: str,
    phone: str = "",
    name: str = "",
    email: str = "",
    city: str = "",
    source: str = "",
    project_type: str = "",
    budget_range: str = "",
    score: int = 0,
    path: str = "chat",
    extra_data: dict | None = None,
    score_breakdown: dict | None = None,
    raw_payload: dict | None = None,
    duplicate_of: str | None = None,
) -> str:
    row = await pool.fetchrow(
        """
        INSERT INTO qualifier_leads
          (company_id, lead_id, phone, name, email, city, source, project_type,
           budget_range, score, path, extra_data, score_breakdown, raw_payload, duplicate_of)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        ON CONFLICT (company_id, lead_id) DO UPDATE
          SET score           = EXCLUDED.score,
              path            = EXCLUDED.path,
              name            = COALESCE(NULLIF(EXCLUDED.name, ''), qualifier_leads.name),
              phone           = COALESCE(NULLIF(EXCLUDED.phone, ''), qualifier_leads.phone),
              email           = COALESCE(NULLIF(EXCLUDED.email, ''), qualifier_leads.email),
              city            = COALESCE(NULLIF(EXCLUDED.city, ''), qualifier_leads.city),
              source          = COALESCE(NULLIF(EXCLUDED.source, ''), qualifier_leads.source),
              project_type    = COALESCE(NULLIF(EXCLUDED.project_type, ''), qualifier_leads.project_type),
              budget_range    = COALESCE(NULLIF(EXCLUDED.budget_range, ''), qualifier_leads.budget_range),
              extra_data      = EXCLUDED.extra_data,
              score_breakdown = EXCLUDED.score_breakdown,
              raw_payload     = COALESCE(EXCLUDED.raw_payload, qualifier_leads.raw_payload),
              duplicate_of    = COALESCE(EXCLUDED.duplicate_of, qualifier_leads.duplicate_of),
              updated_at      = now()
        RETURNING id
        """,
        company_id, lead_id, phone, name, email, city, source, project_type,
        budget_range, score, path,
        json.dumps(extra_data or {}),
        json.dumps(score_breakdown) if score_breakdown else None,
        json.dumps(raw_payload, ensure_ascii=False) if raw_payload else None,
        duplicate_of,
    )
    db_id = str(row["id"])
    log.info("qualifier_lead_upserted", db_id=db_id, lead_id=lead_id, score=score)
    return db_id


async def list_leads(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    limit: int = 100,
    offset: int = 0,
    status: str | None = None,
    score_min: int | None = None,
    score_max: int | None = None,
    source: str | None = None,
    path: str | None = None,
    search: str | None = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    # Build dynamic WHERE clause
    conditions = ["company_id = $1"]
    params: list[Any] = [company_id]
    idx = 2

    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    if score_min is not None:
        conditions.append(f"score >= ${idx}")
        params.append(score_min)
        idx += 1
    if score_max is not None:
        conditions.append(f"score <= ${idx}")
        params.append(score_max)
        idx += 1
    if source:
        conditions.append(f"extra_data->>'source' = ${idx}")
        params.append(source)
        idx += 1
    if path:
        conditions.append(f"path = ${idx}")
        params.append(path)
        idx += 1
    if search:
        conditions.append(f"(name ILIKE ${idx} OR phone ILIKE ${idx})")
        params.append(f"%{search}%")
        idx += 1
    if date_from:
        conditions.append(f"created_at >= ${idx}::timestamptz")
        params.append(date_from)
        idx += 1
    if date_to:
        conditions.append(f"created_at <= ${idx}::timestamptz")
        params.append(date_to)
        idx += 1

    where = " AND ".join(conditions)

    # Whitelist sortable columns
    allowed_sort = {"created_at", "score", "name", "status", "path"}
    col = sort_by if sort_by in allowed_sort else "created_at"
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    # Count total
    total_row = await pool.fetchrow(
        f"SELECT COUNT(*) AS cnt FROM qualifier_leads WHERE {where}", *params,
    )
    total = total_row["cnt"] if total_row else 0

    # Fetch page
    params.append(limit)
    params.append(offset)
    rows = await pool.fetch(
        f"""
        SELECT id, lead_id, phone, name, email, city, source,
               project_type, budget_range, score, path, status,
               extra_data, score_breakdown, raw_payload, duplicate_of,
               assigned_to, created_at, updated_at
        FROM qualifier_leads
        WHERE {where}
        ORDER BY {col} {direction}
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
    )

    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        if d.get("duplicate_of"):
            d["duplicate_of"] = str(d["duplicate_of"])
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
        d["updated_at"] = d["updated_at"].isoformat() if d["updated_at"] else None
        for field in ("extra_data", "score_breakdown", "raw_payload"):
            if isinstance(d.get(field), str):
                d[field] = json.loads(d[field])
        result.append(d)

    return {"data": result, "total": total, "limit": limit, "offset": offset}


async def get_lead_with_session(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    lead_db_id: str,
) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT l.id, l.lead_id, l.phone, l.name, l.score, l.path, l.status,
               l.extra_data, l.score_breakdown, l.raw_payload, l.duplicate_of,
               l.created_at, l.updated_at,
               s.id AS session_id, s.score AS session_score,
               s.stage, s.champ_json, s.messages,
               s.created_at AS session_created_at,
               h.final_score, h.reasoning_json, h.champ_json AS handoff_champ_json,
               h.crm_sent, h.sent_at
        FROM qualifier_leads l
        LEFT JOIN qualifier_sessions s ON s.lead_id = l.id
        LEFT JOIN qualifier_handoffs h ON h.lead_id = l.id
        WHERE l.company_id = $1 AND l.id = $2::uuid
        ORDER BY s.created_at DESC
        LIMIT 1
        """,
        company_id, lead_db_id,
    )
    if row is None:
        return None

    d = dict(row)
    d["id"] = str(d["id"])
    if d.get("duplicate_of"):
        d["duplicate_of"] = str(d["duplicate_of"])
    if d["session_id"]:
        d["session_id"] = str(d["session_id"])
    d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
    d["updated_at"] = d["updated_at"].isoformat() if d["updated_at"] else None
    d["session_created_at"] = d["session_created_at"].isoformat() if d.get("session_created_at") else None
    d["sent_at"] = d["sent_at"].isoformat() if d.get("sent_at") else None
    for field in ("extra_data", "score_breakdown", "raw_payload", "champ_json", "messages", "reasoning_json", "handoff_champ_json"):
        if isinstance(d.get(field), str):
            d[field] = json.loads(d[field])
    return d


async def check_phone_duplicate(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    phone: str,
    hours: int = 24,
) -> dict | None:
    """Son {hours} saat içinde aynı company+phone ile lead var mı?"""
    if not phone:
        return None
    row = await pool.fetchrow(
        """
        SELECT id, lead_id, name, created_at
        FROM qualifier_leads
        WHERE company_id = $1 AND phone = $2
          AND created_at > now() - make_interval(hours => $3)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        company_id, phone, hours,
    )
    if row is None:
        return None
    return {"id": str(row["id"]), "lead_id": row["lead_id"], "name": row["name"]}


async def update_lead_status(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    lead_id: str,
    status: str,
    score: int | None = None,
) -> None:
    if score is not None:
        await pool.execute(
            "UPDATE qualifier_leads SET status=$1, score=$2, updated_at=now() "
            "WHERE company_id=$3 AND lead_id=$4",
            status, score, company_id, lead_id,
        )
    else:
        await pool.execute(
            "UPDATE qualifier_leads SET status=$1, updated_at=now() "
            "WHERE company_id=$2 AND lead_id=$3",
            status, company_id, lead_id,
        )


# ── Activity Log ────────────────────────────────────────────────────────────

async def insert_activity(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    lead_db_id: str,
    event_type: str,
    actor: str = "system",
    payload: dict | None = None,
) -> str:
    row = await pool.fetchrow(
        """
        INSERT INTO activity_log (company_id, lead_db_id, event_type, actor, payload)
        VALUES ($1, $2::uuid, $3, $4, $5)
        RETURNING id
        """,
        company_id, lead_db_id, event_type, actor,
        json.dumps(payload or {}),
    )
    return str(row["id"])


async def list_activities(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    lead_db_id: str,
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
) -> dict[str, Any]:
    conditions = ["company_id = $1", "lead_db_id = $2::uuid"]
    params: list[Any] = [company_id, lead_db_id]
    idx = 3
    if event_type:
        conditions.append(f"event_type = ${idx}")
        params.append(event_type)
        idx += 1

    where = " AND ".join(conditions)

    total_row = await pool.fetchrow(
        f"SELECT COUNT(*) AS cnt FROM activity_log WHERE {where}", *params,
    )
    total = total_row["cnt"] if total_row else 0

    params.append(limit)
    params.append(offset)
    rows = await pool.fetch(
        f"""
        SELECT id, event_type, actor, payload, created_at
        FROM activity_log WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
    )
    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
        if isinstance(d.get("payload"), str):
            d["payload"] = json.loads(d["payload"])
        result.append(d)
    return {"data": result, "total": total}


# ── Status Update (with activity log) ──────────────────────────────────────

VALID_TRANSITIONS: dict[str, set[str]] = {
    "new": {"contacted", "qualifying", "qualified", "lost", "archived"},
    "contacted": {"new", "qualifying", "qualified", "lost", "archived"},
    "qualifying": {"contacted", "qualified", "lost", "archived"},
    "qualified": {"qualifying", "negotiation", "won", "lost", "archived"},
    "negotiation": {"qualified", "won", "lost", "archived"},
    "won": {"negotiation", "archived"},
    "lost": {"new", "qualifying", "archived"},
    "archived": {"new", "qualifying"},
}


async def update_lead_status_with_log(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    lead_db_id: str,
    new_status: str,
    actor: str = "system",
    reason: str = "",
) -> dict:
    # Get current status
    row = await pool.fetchrow(
        "SELECT status FROM qualifier_leads WHERE company_id = $1 AND id = $2::uuid",
        company_id, lead_db_id,
    )
    if row is None:
        raise ValueError("lead_not_found")

    old_status = row["status"]
    allowed = VALID_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise ValueError(f"invalid_transition:{old_status}:{new_status}")

    # Update + log in one connection
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE qualifier_leads SET status=$1, updated_at=now() WHERE id=$2::uuid",
                new_status, lead_db_id,
            )
            activity_id = await conn.fetchval(
                """
                INSERT INTO activity_log (company_id, lead_db_id, event_type, actor, payload)
                VALUES ($1, $2::uuid, 'status_change', $3, $4)
                RETURNING id
                """,
                company_id, lead_db_id, actor,
                json.dumps({"old_status": old_status, "new_status": new_status, "reason": reason}),
            )

    log.info("lead_status_changed", lead_db_id=lead_db_id, old=old_status, new=new_status)
    return {"activity_id": str(activity_id), "previous_status": old_status, "new_status": new_status}


# ── Assignment ──────────────────────────────────────────────────────────────

async def assign_lead(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    lead_db_id: str,
    assigned_to: str,
    assigned_by: str,
) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            old = await conn.fetchval(
                "SELECT assigned_to FROM qualifier_leads WHERE id=$1::uuid AND company_id=$2",
                lead_db_id, company_id,
            )
            await conn.execute(
                "UPDATE qualifier_leads SET assigned_to=$1, updated_at=now() WHERE id=$2::uuid",
                assigned_to, lead_db_id,
            )
            await conn.execute(
                """
                INSERT INTO activity_log (company_id, lead_db_id, event_type, actor, payload)
                VALUES ($1, $2::uuid, 'assignment', $3, $4)
                """,
                company_id, lead_db_id, assigned_by,
                json.dumps({"old_assigned_to": old, "new_assigned_to": assigned_to}),
            )
    return {"ok": True, "assigned_to": assigned_to}


# ── Handoffs ───────────────────────────────────────────────────────────────

async def list_handoffs(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    total_row = await pool.fetchrow(
        "SELECT COUNT(*) AS cnt FROM qualifier_handoffs WHERE company_id=$1", company_id,
    )
    total = total_row["cnt"] if total_row else 0

    rows = await pool.fetch(
        """
        SELECT h.id, h.lead_id, h.final_score, h.crm_sent, h.sent_at, h.created_at,
               l.name, l.phone
        FROM qualifier_handoffs h
        JOIN qualifier_leads l ON l.id = h.lead_id
        WHERE h.company_id = $1
        ORDER BY h.created_at DESC
        LIMIT $2 OFFSET $3
        """,
        company_id, limit, offset,
    )
    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        d["lead_id"] = str(d["lead_id"])
        d["sent_at"] = d["sent_at"].isoformat() if d["sent_at"] else None
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
        result.append(d)
    return {"data": result, "total": total}


async def list_active_sessions(
    pool: asyncpg.Pool,
    *,
    company_id: str,
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT s.id AS session_id, s.stage, s.score AS session_score,
               s.created_at AS session_created_at,
               l.id AS lead_id, l.name, l.phone,
               jsonb_array_length(COALESCE(s.messages, '[]'::jsonb)) AS msg_count
        FROM qualifier_sessions s
        JOIN qualifier_leads l ON l.id = s.lead_id
        WHERE s.company_id = $1
          AND s.stage NOT IN ('HANDOFF', 'handoff')
        ORDER BY s.created_at DESC
        """,
        company_id,
    )
    result = []
    for row in rows:
        d = dict(row)
        d["session_id"] = str(d["session_id"])
        d["lead_id"] = str(d["lead_id"])
        d["session_created_at"] = d["session_created_at"].isoformat() if d["session_created_at"] else None
        result.append(d)
    return result


# ── Bulk Operations ─────────────────────────────────────────────────────────

async def bulk_update_status(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    lead_ids: list[str],
    new_status: str,
    actor: str = "dashboard",
) -> dict:
    affected = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for lid in lead_ids[:200]:
                result = await conn.execute(
                    "UPDATE qualifier_leads SET status=$1, updated_at=now() WHERE id=$2::uuid AND company_id=$3",
                    new_status, lid, company_id,
                )
                if result == "UPDATE 1":
                    affected += 1
                    await conn.execute(
                        "INSERT INTO activity_log (company_id, lead_db_id, event_type, actor, payload) VALUES ($1,$2::uuid,'status_change',$3,$4)",
                        company_id, lid, actor, json.dumps({"new_status": new_status, "bulk": True}),
                    )
    return {"affected": affected, "total": len(lead_ids)}


async def bulk_assign(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    lead_ids: list[str],
    assigned_to: str,
    actor: str = "dashboard",
) -> dict:
    affected = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for lid in lead_ids[:200]:
                result = await conn.execute(
                    "UPDATE qualifier_leads SET assigned_to=$1, updated_at=now() WHERE id=$2::uuid AND company_id=$3",
                    assigned_to, lid, company_id,
                )
                if result == "UPDATE 1":
                    affected += 1
                    await conn.execute(
                        "INSERT INTO activity_log (company_id, lead_db_id, event_type, actor, payload) VALUES ($1,$2::uuid,'assignment',$3,$4)",
                        company_id, lid, actor, json.dumps({"new_assigned_to": assigned_to, "bulk": True}),
                    )
    return {"affected": affected, "total": len(lead_ids)}


# ── CSV Export ──────────────────────────────────────────────────────────────

async def export_leads_csv(
    pool: asyncpg.Pool,
    *,
    company_id: str,
    status: str | None = None,
    score_min: int | None = None,
    score_max: int | None = None,
    source: str | None = None,
    path: str | None = None,
) -> list[dict]:
    """Fetch all matching leads for CSV export (no pagination)."""
    conditions = ["company_id = $1"]
    params: list[Any] = [company_id]
    idx = 2

    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    if score_min is not None:
        conditions.append(f"score >= ${idx}")
        params.append(score_min)
        idx += 1
    if score_max is not None:
        conditions.append(f"score <= ${idx}")
        params.append(score_max)
        idx += 1
    if source:
        conditions.append(f"extra_data->>'source' = ${idx}")
        params.append(source)
        idx += 1
    if path:
        conditions.append(f"path = ${idx}")
        params.append(path)
        idx += 1

    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT id, lead_id, phone, name, score, path, status, assigned_to,
               extra_data, score_breakdown, created_at, updated_at
        FROM qualifier_leads
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT 5000
        """,
        *params,
    )
    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else ""
        d["updated_at"] = d["updated_at"].isoformat() if d["updated_at"] else ""
        for field in ("extra_data", "score_breakdown"):
            if isinstance(d.get(field), str):
                d[field] = json.loads(d[field])
        result.append(d)
    return result
