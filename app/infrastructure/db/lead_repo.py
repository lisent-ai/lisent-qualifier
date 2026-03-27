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
    phone: str,
    name: str = "",
    score: int = 0,
    path: str = "chat",
    extra_data: dict | None = None,
) -> str:
    row = await pool.fetchrow(
        """
        INSERT INTO qualifier_leads
          (company_id, lead_id, phone, name, score, path, extra_data)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        ON CONFLICT (company_id, lead_id) DO UPDATE
          SET score      = EXCLUDED.score,
              path       = EXCLUDED.path,
              extra_data = EXCLUDED.extra_data,
              updated_at = now()
        RETURNING id
        """,
        company_id, lead_id, phone, name, score, path,
        json.dumps(extra_data or {}),
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
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, lead_id, phone, name, score, path, status,
               extra_data, created_at, updated_at
        FROM qualifier_leads
        WHERE company_id = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
        """,
        company_id, limit, offset,
    )
    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
        d["updated_at"] = d["updated_at"].isoformat() if d["updated_at"] else None
        if isinstance(d["extra_data"], str):
            d["extra_data"] = json.loads(d["extra_data"])
        result.append(d)
    return result


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
