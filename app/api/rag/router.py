"""RAG webhook + admin endpoints.

Public (token-in-URL, rate-limited):
  POST /webhook/rag/{token}                 — ingest documents
  DELETE /webhook/rag/{token}/documents/{doc_ref} — delete one document

Internal (X-API-Key):
  GET    /internal/rag/{company_id}/documents — list docs the company uploaded
  POST   /internal/rag/{company_id}/search    — debug retrieval for the Hub

Token lookup is delegated to the CRM service (:func:`lookup_company_by_rag_token`);
the qualifier never stores company tokens locally.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.infrastructure.crm.rest_client import lookup_company_by_rag_token
from app.infrastructure.rag import repository as kb_repo
from app.infrastructure.redis.client import get_redis

log = structlog.get_logger(__name__)

router = APIRouter(tags=["rag"])


# ── Request / response models ───────────────────────────────────────────────

class IngestDocument(BaseModel):
    """One document in the batch. ``content`` is required; everything else
    is optional. ``record_id`` is the idempotency key — re-sending with the same
    ref replaces the existing chunks rather than creating duplicates."""

    model_config = ConfigDict(populate_by_name=True)

    doc_ref: str = Field(alias="record_id", min_length=1, max_length=200)
    title: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=200_000)
    source_url: Optional[str] = Field(default=None, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestRequest(BaseModel):
    data: list[IngestDocument] = Field(min_length=1, max_length=50)


class IngestResult(BaseModel):
    doc_ref: str
    chunk_count: int


class IngestResponse(BaseModel):
    accepted: int
    results: list[IngestResult]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)


# ── Public ingestion endpoint ────────────────────────────────────────────────

async def _rate_limit(company_id: str) -> None:
    """10 req/s sustained, burst 50, evaluated per company. Best-effort: if
    Redis is down we fall back to allowing the request rather than black-
    holing ingestion during a Redis outage."""

    try:
        redis = get_redis()
    except Exception:
        return
    now = int(time.time())
    key = f"rag_rl:{company_id}:{now}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 10)
        if count > 50:
            raise HTTPException(status_code=429, detail="rag ingestion rate limit exceeded")
    except HTTPException:
        raise
    except Exception:
        return


@router.post("/webhook/rag/{token}", response_model=IngestResponse, status_code=202)
async def ingest_documents(
    request: Request,
    token: str = Path(..., min_length=10, max_length=200),
    payload: IngestRequest = Body(...),
    idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> IngestResponse:
    settings = get_settings()
    if not settings.rag_webhook_enabled:
        raise HTTPException(status_code=503, detail="RAG webhook disabled")

    company = await lookup_company_by_rag_token(token)
    if not company:
        raise HTTPException(status_code=401, detail="invalid or revoked rag token")

    company_id = str(company.get("company_id") or "").strip()
    if not company_id:
        raise HTTPException(status_code=500, detail="company lookup malformed")

    await _rate_limit(company_id)

    # Idempotency: de-dup identical payloads within 24h. A second call with
    # the same header returns the original result rather than re-ingesting.
    if idempotency_key:
        try:
            redis = get_redis()
            cached = await redis.get(f"rag_idem:{company_id}:{idempotency_key}")
            if cached:
                import json

                return IngestResponse(**json.loads(cached))
        except Exception:
            pass

    results: list[IngestResult] = []
    for doc in payload.data:
        chunk_count = await kb_repo.upsert_document(
            company_id=company_id,
            doc_ref=doc.doc_ref,
            title=doc.title,
            content=doc.content,
            source_url=doc.source_url,
            metadata=doc.metadata,
        )
        results.append(IngestResult(doc_ref=doc.doc_ref, chunk_count=chunk_count))

    response = IngestResponse(accepted=len(results), results=results)

    if idempotency_key:
        try:
            import json

            redis = get_redis()
            await redis.set(
                f"rag_idem:{company_id}:{idempotency_key}",
                json.dumps(response.model_dump()),
                ex=86_400,
            )
        except Exception:
            pass

    log.info(
        "rag_ingest_accepted",
        company_id=company_id,
        document_count=len(results),
        chunk_total=sum(r.chunk_count for r in results),
    )
    return response


@router.delete("/webhook/rag/{token}/documents/{doc_ref}", status_code=200)
async def delete_document(
    token: str = Path(..., min_length=10, max_length=200),
    doc_ref: str = Path(..., min_length=1, max_length=200),
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.rag_webhook_enabled:
        raise HTTPException(status_code=503, detail="RAG webhook disabled")

    company = await lookup_company_by_rag_token(token)
    if not company:
        raise HTTPException(status_code=401, detail="invalid or revoked rag token")

    company_id = str(company.get("company_id") or "").strip()
    removed = await kb_repo.delete_document(company_id, doc_ref)
    return {"doc_ref": doc_ref, "deleted_chunks": removed}


# ── Internal admin endpoints ────────────────────────────────────────────────

def _require_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> None:
    settings = get_settings()
    expected = settings.internal_api_key
    if not expected:
        return  # API key gating disabled — dev only.
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


@router.get("/internal/rag/{company_id}/documents", dependencies=[Depends(_require_api_key)])
async def list_company_documents(
    company_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return await kb_repo.list_documents(company_id, limit=limit)


@router.get(
    "/internal/rag/{company_id}/documents/{doc_ref}",
    dependencies=[Depends(_require_api_key)],
)
async def get_company_document(company_id: str, doc_ref: str) -> dict[str, Any]:
    chunks = await kb_repo.get_document_chunks(company_id, doc_ref)
    if not chunks:
        raise HTTPException(status_code=404, detail="document not found")
    return {"doc_ref": doc_ref, "chunks": kb_repo.as_dicts(chunks)}


@router.post("/internal/rag/{company_id}/search", dependencies=[Depends(_require_api_key)])
async def debug_search(company_id: str, body: SearchRequest) -> dict[str, Any]:
    chunks = await kb_repo.search_chunks(company_id, body.query, top_k=body.top_k)
    return {"hits": kb_repo.as_dicts(chunks)}
