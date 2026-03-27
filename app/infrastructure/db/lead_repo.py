"""
qualifier_leads tablosuna yazma/okuma işlemleri.
"""
import json
from uuid import UUID

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
    email: str = "",
    city: str = "",
    source: str = "other",
    project_type: str = "other",
    budget_range: str = "unknown",
    score: int = 0,
    path: str = "chat",
    raw_payload: dict,
) -> str:
    """
    qualifier_leads tablosuna lead ekler. lead_id zaten varsa score ve path günceller.
    Eklenen/güncellenen satırın UUID'sini döner.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO qualifier_leads
          (company_id, lead_id, phone, name, email, city,
           source, project_type, budget_range, score, path, raw_payload)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        ON CONFLICT (company_id, lead_id) DO UPDATE
          SET score       = EXCLUDED.score,
              path        = EXCLUDED.path,
              updated_at  = now()
        RETURNING id
        """,
        company_id, lead_id, phone, name, email, city,
        source, project_type, budget_range, score, path,
        json.dumps(raw_payload),
    )
    db_id = str(row["id"])
    log.info("qualifier_lead_upserted", db_id=db_id, lead_id=lead_id, score=score)
    return db_id


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
