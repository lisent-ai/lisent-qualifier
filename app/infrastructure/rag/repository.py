"""PostgreSQL-backed knowledge-base repository for the RAG pipeline.

Chunks of ingested documents are stored in ``ai_kb_documents``. Retrieval runs
on a ``tsvector`` GIN index so it works without an embedding provider; the
``embedding`` column stays in place for a future pgvector swap-in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import asyncpg
import structlog

from app.infrastructure.db.pool import get_db_pool

log = structlog.get_logger(__name__)

# Keep chunks small enough to fit comfortably in a Groq chat prompt along with
# the user turn history. Overlap lets a sentence that straddles a chunk
# boundary still match a similarity query on either side.
CHUNK_SIZE_CHARS = 1200
CHUNK_OVERLAP_CHARS = 160


@dataclass
class KBChunk:
    id: str
    company_id: str
    doc_ref: str
    chunk_index: int
    title: str
    content: str
    source_url: str | None
    metadata: dict[str, Any]


def chunk_text(text: str, *, size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Split ``text`` into roughly-``size`` chunks, preferring sentence ends.

    Produces at least one chunk (empty string yields an empty list). Never
    returns empty-string chunks — whitespace-only tails are dropped.
    """

    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    n = len(normalized)
    while start < n:
        end = min(start + size, n)
        # Prefer to break at the last sentence boundary inside the window.
        if end < n:
            for delim in (". ", "! ", "? ", "\n", "; "):
                cut = normalized.rfind(delim, start, end)
                if cut != -1 and cut > start + int(size * 0.5):
                    end = cut + len(delim)
                    break
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


async def upsert_document(
    *,
    company_id: str,
    doc_ref: str,
    title: str,
    content: str,
    source_url: str | None,
    metadata: dict[str, Any] | None,
) -> int:
    """Replace all chunks for (company_id, doc_ref) with fresh ones.

    Returns the number of chunks written. Idempotent — calling twice with
    the same content produces the same final state.
    """

    pool = get_db_pool()
    pieces = chunk_text(content)
    if not pieces:
        return 0

    meta = metadata or {}
    async with pool.acquire() as conn:  # type: asyncpg.Connection
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM ai_kb_documents WHERE company_id = $1 AND doc_ref = $2",
                company_id,
                doc_ref,
            )
            rows = [
                (company_id, doc_ref, idx, title, piece, source_url, _to_jsonb(meta))
                for idx, piece in enumerate(pieces)
            ]
            await conn.executemany(
                """
                INSERT INTO ai_kb_documents
                  (company_id, doc_ref, chunk_index, title, content, source_url, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                """,
                rows,
            )
    log.info(
        "rag_doc_upserted",
        company_id=company_id,
        doc_ref=doc_ref,
        chunk_count=len(pieces),
    )
    return len(pieces)


async def delete_document(company_id: str, doc_ref: str) -> int:
    pool = get_db_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM ai_kb_documents WHERE company_id = $1 AND doc_ref = $2",
            company_id,
            doc_ref,
        )
    # asyncpg returns a status tag like "DELETE 5"; extract the row count.
    try:
        return int(status.rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def list_documents(company_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return one row per distinct doc_ref with aggregate stats."""

    pool = get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT doc_ref,
                   MAX(title) AS title,
                   MAX(source_url) AS source_url,
                   COUNT(*) AS chunk_count,
                   MAX(updated_at) AS updated_at
            FROM ai_kb_documents
            WHERE company_id = $1
            GROUP BY doc_ref
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            company_id,
            limit,
        )
    return [
        {
            "doc_ref": r["doc_ref"],
            "title": r["title"],
            "source_url": r["source_url"],
            "chunk_count": r["chunk_count"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in rows
    ]


async def search_chunks(
    company_id: str,
    query: str,
    *,
    top_k: int = 3,
) -> list[KBChunk]:
    """Full-text similarity search scoped to a single company.

    Uses the Postgres tsvector GIN index built in migration 002. Empty or
    whitespace-only queries return no rows rather than the entire corpus.
    """

    normalized = re.sub(r"\s+", " ", query or "").strip()
    if not normalized:
        return []

    pool = get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, company_id, doc_ref, chunk_index, title, content,
                   source_url, metadata,
                   ts_rank(content_tsv, plainto_tsquery('simple', $2)) AS rank
            FROM ai_kb_documents
            WHERE company_id = $1
              AND content_tsv @@ plainto_tsquery('simple', $2)
            ORDER BY rank DESC
            LIMIT $3
            """,
            company_id,
            normalized,
            top_k,
        )

    return [
        KBChunk(
            id=str(r["id"]),
            company_id=str(r["company_id"]),
            doc_ref=r["doc_ref"],
            chunk_index=r["chunk_index"],
            title=r["title"] or "",
            content=r["content"],
            source_url=r["source_url"],
            metadata=_from_jsonb(r["metadata"]),
        )
        for r in rows
    ]


def _to_jsonb(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)


def _from_jsonb(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    if isinstance(data, (bytes, str)):
        import json

        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {}
    return {}


# Exposed so test harnesses can seed chunks without rebuilding the module.
__all__: Sequence[str] = (
    "CHUNK_SIZE_CHARS",
    "CHUNK_OVERLAP_CHARS",
    "KBChunk",
    "chunk_text",
    "upsert_document",
    "delete_document",
    "list_documents",
    "search_chunks",
)


def as_dicts(chunks: Iterable[KBChunk]) -> list[dict[str, Any]]:
    return [
        {
            "id": c.id,
            "doc_ref": c.doc_ref,
            "chunk_index": c.chunk_index,
            "title": c.title,
            "content": c.content,
            "source_url": c.source_url,
            "metadata": c.metadata,
        }
        for c in chunks
    ]
