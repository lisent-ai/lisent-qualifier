import asyncio
import hashlib
import json as _json
import time
import uuid

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import get_lead_intake_handler, SessionRepoDep, ScoreRepoDep
from app.application.lead_intake.commands import ProcessWebhookLeadCommand
from app.infrastructure.crm.rest_client import (
    bulk_store_webhook_data,
    lookup_company_by_qualifier_token,
    lookup_company_by_rag_token,
    store_webhook_data,
)
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.db.lead_repo import upsert_lead
from app.infrastructure.llm.field_mapper import map_fields
from app.infrastructure.redis.client import get_redis
from app.api.webhook.rate_limit import check_rate_limit
import structlog

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])

_MAX_LEAD_BODY_SIZE = 64 * 1024          # 64 KB — lead webhook
_MAX_RAG_BODY_SIZE = 100 * 1024 * 1024   # 100 MB — RAG webhook (legacy)
_MAX_RAG_CHUNKED_BODY = 2 * 1024 * 1024  # 2 MB — sender-chunked payload
_RAG_CHUNK_SIZE = 500                     # rows per chunk (legacy + max per sender chunk)
_RAG_STORE_CONCURRENCY = 5               # parallel CRM calls
_RAG_MAX_RETRIES = 5                     # retries per chunk
_RAG_FAILED_TTL = 7 * 86_400            # 7 days in Redis
_RAG_BATCH_TTL = 3600                    # 1 hour — incomplete batch timeout
_RAG_QUEUE_KEY = "rag:ingest:queue"      # Redis list — processing queue
_RAG_QUEUE_DEPTH_LIMIT = 10_000          # backpressure threshold

# ── ID field names commonly used by form/CRM platforms ─────────────────────
_EXTERNAL_ID_KEYS = (
    "lead_id", "leadId", "externalLeadId", "external_lead_id",
    "id", "form_id", "formId", "submission_id", "submissionId",
    "entry_id", "entryId", "record_id", "recordId",
    "ref", "reference", "kayit_id", "basvuru_no",
)


def _extract_external_id(payload: dict) -> str | None:
    """Search raw payload (top-level + nested 'data') for a lead identifier."""
    for key in _EXTERNAL_ID_KEYS:
        val = payload.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()

    data = payload.get("data")
    if isinstance(data, dict):
        for key in _EXTERNAL_ID_KEYS:
            val = data.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()

    return None


@router.post("/lead/{company_token}", status_code=status.HTTP_202_ACCEPTED)
async def receive_lead(
    company_token: str,
    request: Request,
    session_repo: SessionRepoDep,
    score_repo: ScoreRepoDep,
) -> dict:
    # ── Body size check + JSON parse ────────────────────────────────────────
    body = await request.body()
    if len(body) > _MAX_LEAD_BODY_SIZE:
        return JSONResponse({"error": "payload too large"}, status_code=413)

    try:
        raw_payload: dict = _json.loads(body)
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    if not isinstance(raw_payload, dict):
        return JSONResponse({"error": "payload must be a JSON object"}, status_code=400)

    # ── Token → company_id + fallback_url ─────────────���─────────────────────
    company_info = await lookup_company_by_qualifier_token(company_token)
    if not company_info:
        log.warning("qualifier_token_invalid", token=company_token[-8:])
        return JSONResponse({"error": "invalid token"}, status_code=401)

    company_id = str(company_info["company_id"])
    fallback_url: str | None = company_info.get("fallback_url")

    # ── Field mapping (heuristic + LLM fallback) ───────────────────────────
    mapping = await map_fields(raw_payload)

    # ── lead_id: external_id > payload lookup > auto UUID ─────────────────
    lead_id = mapping.external_id or _extract_external_id(raw_payload) or str(uuid.uuid4())

    # ── Build lead_data for handler ─────────��─────────────────────���─────────
    lead_data = {
        "name": mapping.full_name,
        "phone": mapping.phone,
        "email": mapping.email,
        "city": mapping.city,
        "source": mapping.source,
        "project_type": mapping.project_type,
        "budget_range": mapping.budget_range,
        "budget_amount": mapping.budget_amount,
        "decision_authority": mapping.decision_authority,
        "timeline_urgency": mapping.timeline_urgency,
        "notes": mapping.notes,
        "lead_id": lead_id,
        "extra_data": mapping.extra_fields,
        "raw_payload": raw_payload,
    }

    # ── AI akışını çalıştır ─────────────────────────────────────────────────
    handler = get_lead_intake_handler(session_repo, score_repo)
    cmd = ProcessWebhookLeadCommand(
        lead_data=lead_data,
        fallback_url=fallback_url,
        company_id=company_id,
    )
    result = await handler.handle(cmd)



    # ── PostgreSQL'e kaydet ────────────────────────────────────────────────
    result_status = result.get("status", "chat_path")

    # Duplicate from Redis idempotency — skip DB write entirely,
    # the lead already exists from the first webhook call.
    if result_status == "duplicate":
        log.info("duplicate_skip_db", lead_id=lead_id)
        return result

    path = result_status.replace("_path", "")
    # Fast path → lead direkt qualified, chat path → new (AI bekliyor)
    lead_status = "qualified" if result_status == "fast_path" else "new"

    try:
        pool = get_db_pool()
        db_id = await upsert_lead(
            pool,
            company_id=company_id,
            lead_id=lead_id,
            phone=mapping.phone,
            name=mapping.full_name,
            email=mapping.email,
            city=mapping.city,
            source=mapping.source,
            project_type=mapping.project_type,
            budget_range=mapping.budget_range,
            score=result.get("score", 0),
            path=path,
            extra_data=mapping.extra_fields,
            score_breakdown=result.get("score_breakdown"),
            raw_payload=raw_payload,
        )

        # Fast path → status direkt qualified olarak güncelle
        if lead_status == "qualified" and db_id:
            await pool.execute(
                "UPDATE qualifier_leads SET status='qualified', updated_at=now() WHERE id=$1::uuid",
                db_id,
            )

        # Chat path ise qualifier_sessions tablosuna da yaz
        session_id = result.get("session_id")
        if session_id and db_id:
            await _upsert_session(pool, db_id, company_id, session_id, result.get("score", 0))

    except Exception as exc:
        log.error("qualifier_db_write_failed", error=str(exc), lead_id=lead_id)

    return result


@router.post("/rag/{token}", status_code=status.HTTP_202_ACCEPTED)
async def receive_rag_data(token: str, request: Request) -> dict:
    """Accept RAG data in two formats:

    **Sender-chunked format** (preferred):
        Payload contains ``batch_id``, ``chunk_index``, ``total_chunks``,
        ``checksum``, and ``data[]`` with ``record_id`` per record.
        Each request is one chunk — the sender splits beforehand.

    **Legacy format** (backward-compatible):
        Any JSON array or object.  Chunked server-side into
        ``_RAG_CHUNK_SIZE``-row pieces and stored asynchronously.

    Failed chunks are persisted to Redis (7-day TTL) so **no data is lost**.
    """
    body = await request.body()

    # ── Rate limiting ──────────────────────────────────────────────────────
    allowed, retry_after = await check_rate_limit(token)
    if not allowed:
        return JSONResponse(
            {"error": "rate limit exceeded"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    # ── Backpressure check ─────────────────────────────────────────────────
    try:
        redis = get_redis()
        queue_depth = await redis.llen(_RAG_QUEUE_KEY)
        if queue_depth >= _RAG_QUEUE_DEPTH_LIMIT:
            log.warning("rag_backpressure", queue_depth=queue_depth)
            return JSONResponse(
                {"error": "server busy, try again later"},
                status_code=503,
                headers={"Retry-After": "30"},
            )
    except Exception:
        pass  # Redis down — don't block ingestion

    # ── Token → company_id ─────────────────────────────────────────────────
    company_info = await lookup_company_by_rag_token(token)
    if not company_info:
        log.warning("rag_token_invalid", token=token[-8:])
        return JSONResponse({"error": "invalid token"}, status_code=401)

    company_id = str(company_info["company_id"])

    # ── Detect format: sender-chunked vs legacy ────────────────────────────
    try:
        payload = _json.loads(body)
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    if isinstance(payload, dict) and "batch_id" in payload and "chunk_index" in payload:
        return await _handle_chunked_rag(payload, body, company_id, token)

    return await _handle_legacy_rag(payload, body, company_id)


# ── Sender-chunked handler ─────────────────────────────────────────────────


async def _handle_chunked_rag(
    payload: dict, raw_body: bytes, company_id: str, token: str,
) -> JSONResponse | dict:
    """Process a single sender-side chunk with idempotency and batch tracking."""

    # ── Size limit for chunked payloads ────────────────────────────────────
    if len(raw_body) > _MAX_RAG_CHUNKED_BODY:
        return JSONResponse(
            {"error": f"chunk too large, max {_MAX_RAG_CHUNKED_BODY // (1024 * 1024)}MB"},
            status_code=413,
        )

    # ── Validate required fields ───────────────────────────────────────────
    batch_id = payload.get("batch_id")
    chunk_index = payload.get("chunk_index")
    total_chunks = payload.get("total_chunks")
    checksum = payload.get("checksum")
    data = payload.get("data")

    errors = []
    if not batch_id or not isinstance(batch_id, str):
        errors.append("batch_id is required (string)")
    if chunk_index is None or not isinstance(chunk_index, int) or chunk_index < 0:
        errors.append("chunk_index is required (non-negative integer)")
    if total_chunks is None or not isinstance(total_chunks, int) or total_chunks < 1:
        errors.append("total_chunks is required (positive integer)")
    if not isinstance(data, list) or len(data) == 0:
        errors.append("data is required (non-empty array)")
    if errors:
        return JSONResponse({"error": "validation failed", "details": errors}, status_code=400)

    if chunk_index >= total_chunks:
        return JSONResponse(
            {"error": "chunk_index must be less than total_chunks"}, status_code=400,
        )

    if len(data) > _RAG_CHUNK_SIZE:
        return JSONResponse(
            {"error": f"max {_RAG_CHUNK_SIZE} records per chunk"}, status_code=400,
        )

    # ── Validate record_id on each record ──────────────────────────────────
    for i, record in enumerate(data):
        if not isinstance(record, dict):
            return JSONResponse(
                {"error": f"data[{i}] must be a JSON object"}, status_code=400,
            )
        if not record.get("record_id"):
            return JSONResponse(
                {"error": f"data[{i}].record_id is required"}, status_code=400,
            )

    # ── Checksum verification ──────────────────────────────────────────────
    # Karşı taraf farklı JSON serialization kullanabilir (spacing, key order,
    # unicode escaping vs.). Raw body'den "data" alanını regex ile çıkarıp
    # birden fazla canonical form deniyoruz.
    if checksum:
        bare = checksum.removeprefix("sha256:")
        candidates: list[bytes] = [
            # 1) sort_keys, no ensure_ascii (our original)
            _json.dumps(data, ensure_ascii=False, sort_keys=True).encode(),
            # 2) sort_keys, ensure_ascii (sender may escape unicode)
            _json.dumps(data, ensure_ascii=True, sort_keys=True).encode(),
            # 3) compact, no sort (sender may keep insertion order)
            _json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(),
            # 4) compact + sorted
            _json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(),
            # 5) default spacing (", " and ": ")
            _json.dumps(data, ensure_ascii=False).encode(),
        ]
        matched = any(
            hashlib.sha256(c).hexdigest() == bare for c in candidates
        )
        if not matched:
            log.warning(
                "rag_checksum_mismatch",
                batch_id=batch_id,
                chunk_index=chunk_index,
                received_checksum=checksum[:32],
            )
            return JSONResponse(
                {"error": "checksum mismatch"}, status_code=400,
            )

    # ── Idempotency check ──────────────────────────────────────────────────
    redis = get_redis()
    dedup_key = f"rag_batch:{batch_id}:received"
    already_received = await redis.sismember(dedup_key, str(chunk_index))
    if already_received:
        chunks_received = await redis.scard(dedup_key)
        log.info("rag_chunk_duplicate", batch_id=batch_id, chunk_index=chunk_index)
        return {
            "status": "accepted",
            "batch_id": batch_id,
            "chunk_index": chunk_index,
            "chunks_received": chunks_received,
            "chunks_total": total_chunks,
            "duplicate": True,
        }

    # ── total_chunks conflict check ────────────────────────────────────────
    meta_key = f"rag_batch:{batch_id}:meta"
    existing_total = await redis.hget(meta_key, "total_chunks")
    if existing_total is not None and int(existing_total) != total_chunks:
        return JSONResponse(
            {"error": "total_chunks conflicts with previously submitted value"},
            status_code=409,
        )

    # ── Enqueue to processing queue ────────────────────────────────────────
    queue_item = _json.dumps({
        "batch_id": batch_id,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "company_id": company_id,
        "data": data,
    }, ensure_ascii=False)
    await redis.rpush(_RAG_QUEUE_KEY, queue_item)

    # ── Update batch state ─────────────────────────────────────────────────
    pipe = redis.pipeline(transaction=True)
    pipe.sadd(dedup_key, str(chunk_index))
    pipe.hset(meta_key, mapping={
        "total_chunks": str(total_chunks),
        "company_id": company_id,
        "status": "receiving",
        "updated_at": str(time.time()),
    })
    # Ensure created_at is set only once
    pipe.hsetnx(meta_key, "created_at", str(time.time()))
    pipe.expire(dedup_key, _RAG_BATCH_TTL)
    pipe.expire(meta_key, _RAG_BATCH_TTL)
    await pipe.execute()

    chunks_received = await redis.scard(dedup_key)

    # If all chunks received, update status to processing
    if chunks_received >= total_chunks:
        await redis.hset(meta_key, "status", "processing")

    log.info(
        "rag_chunk_accepted",
        batch_id=batch_id,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        chunks_received=chunks_received,
        company_id=company_id,
        records=len(data),
        payload_bytes=len(raw_body),
    )

    return {
        "status": "accepted",
        "batch_id": batch_id,
        "chunk_index": chunk_index,
        "chunks_received": chunks_received,
        "chunks_total": total_chunks,
    }


# ── Legacy handler (backward-compatible) ───────────────────────────────────


async def _handle_legacy_rag(
    payload: object, raw_body: bytes, company_id: str,
) -> JSONResponse | dict:
    """Accept full payload, chunk server-side, store via CRM.

    Records with record_id are upserted individually via bulk endpoint.
    """

    if len(raw_body) > _MAX_RAG_BODY_SIZE:
        return JSONResponse(
            {"error": f"payload too large, max {_MAX_RAG_BODY_SIZE // (1024 * 1024)}MB"},
            status_code=413,
        )

    if isinstance(payload, list):
        total_rows = len(payload)
        if total_rows == 0:
            return JSONResponse({"error": "empty array"}, status_code=400)
        chunks: list = [
            payload[i : i + _RAG_CHUNK_SIZE]
            for i in range(0, total_rows, _RAG_CHUNK_SIZE)
        ]
    else:
        total_rows = 1
        chunks = [payload]

    batch_id = str(uuid.uuid4())
    total_chunks = len(chunks)

    log.info(
        "rag_webhook_accepted_legacy",
        company_id=company_id,
        batch_id=batch_id,
        total_rows=total_rows,
        total_chunks=total_chunks,
        payload_bytes=len(raw_body),
    )

    # Small payload fast-path: store inline
    if total_chunks == 1:
        label = f"batch:{batch_id}:chunk:1/1"
        chunk = chunks[0]
        # Records with record_id → bulk upsert per record
        if isinstance(chunk, list) and chunk and isinstance(chunk[0], dict) and chunk[0].get("record_id"):
            result = await bulk_store_webhook_data(company_id, chunk, label=label)
        else:
            result = await store_webhook_data(company_id, chunk, label=label)
        if result is None:
            await _persist_failed_chunk(batch_id, company_id, 0, chunk)
            return JSONResponse({"error": "failed to store data"}, status_code=502)
        log.info("rag_webhook_stored_inline", company_id=company_id, batch_id=batch_id)
        return {
            "status": "accepted",
            "company_id": company_id,
            "batch_id": batch_id,
            "total_rows": total_rows,
            "total_chunks": 1,
        }

    # Large payload: fire background task and return immediately
    del payload, raw_body

    asyncio.create_task(
        _process_rag_chunks(batch_id, company_id, chunks, total_rows),
        name=f"rag-batch-{batch_id[:8]}",
    )

    return {
        "status": "accepted",
        "company_id": company_id,
        "batch_id": batch_id,
        "total_rows": total_rows,
        "total_chunks": total_chunks,
    }


# ── Batch status endpoint ──────────────────────────────────────────────────


@router.get("/rag/batch/{batch_id}/status")
async def get_batch_status(batch_id: str) -> dict:
    """Return the current state of a sender-chunked batch."""
    redis = get_redis()
    meta_key = f"rag_batch:{batch_id}:meta"
    received_key = f"rag_batch:{batch_id}:received"
    errors_key = f"rag_batch:{batch_id}:errors"

    meta = await redis.hgetall(meta_key)
    if not meta:
        return JSONResponse({"error": "batch not found"}, status_code=404)

    total_chunks = int(meta.get("total_chunks", 0))
    chunks_received = await redis.scard(received_key)
    chunks_processed = int(meta.get("chunks_processed", 0))
    records_processed = int(meta.get("records_processed", 0))
    records_failed = int(meta.get("records_failed", 0))

    # Derive status
    stored_status = meta.get("status", "receiving")
    if stored_status == "receiving" and chunks_received >= total_chunks:
        stored_status = "processing"
    if stored_status == "processing" and chunks_processed >= total_chunks:
        stored_status = "completed" if records_failed == 0 else "partial"

    # Fetch error details (last 50)
    raw_errors = await redis.lrange(errors_key, 0, 49)
    error_list = [_json.loads(e) for e in raw_errors] if raw_errors else []

    return {
        "batch_id": batch_id,
        "status": stored_status,
        "chunks_received": chunks_received,
        "chunks_total": total_chunks,
        "chunks_processed": chunks_processed,
        "records_processed": records_processed,
        "records_failed": records_failed,
        "errors": error_list,
        "company_id": meta.get("company_id", ""),
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
    }


# ── Queue worker for sender-chunked ingestion ─────────────────────────────


async def run_rag_queue_worker() -> None:
    """Background loop that pops chunks from the Redis queue and stores them.

    Launched once at app startup (see main.py lifespan).
    Each chunk is stored to CRM, then batch state is updated.
    """
    redis = get_redis()
    log.info("rag_queue_worker_started")

    while True:
        try:
            # BLPOP with 2s timeout so we can yield to the event loop
            item = await redis.blpop(_RAG_QUEUE_KEY, timeout=2)
            if item is None:
                continue

            _, raw = item  # (key, value)
            chunk_msg = _json.loads(raw)

            batch_id = chunk_msg["batch_id"]
            chunk_index = chunk_msg["chunk_index"]
            total_chunks = chunk_msg["total_chunks"]
            company_id = chunk_msg["company_id"]
            data = chunk_msg["data"]

            label = f"batch:{batch_id}:chunk:{chunk_index + 1}/{total_chunks}"
            success = await _store_chunk_with_retry(company_id, data, label)

            meta_key = f"rag_batch:{batch_id}:meta"

            if success:
                pipe = redis.pipeline(transaction=True)
                pipe.hincrby(meta_key, "chunks_processed", 1)
                pipe.hincrby(meta_key, "records_processed", len(data))
                pipe.hset(meta_key, "updated_at", str(time.time()))
                await pipe.execute()
            else:
                await _persist_failed_chunk(batch_id, company_id, chunk_index, data)
                pipe = redis.pipeline(transaction=True)
                pipe.hincrby(meta_key, "chunks_processed", 1)
                pipe.hincrby(meta_key, "records_failed", len(data))
                pipe.hset(meta_key, "updated_at", str(time.time()))
                # Record error for status endpoint
                error_entry = _json.dumps({
                    "chunk_index": chunk_index,
                    "error": "failed to store after retries",
                    "record_count": len(data),
                })
                pipe.rpush(f"rag_batch:{batch_id}:errors", error_entry)
                await pipe.execute()

            # Check if batch is complete
            meta = await redis.hgetall(meta_key)
            processed = int(meta.get("chunks_processed", 0))
            if processed >= total_chunks:
                failed = int(meta.get("records_failed", 0))
                final_status = "completed" if failed == 0 else "partial"
                await redis.hset(meta_key, "status", final_status)
                log.info(
                    "rag_batch_complete",
                    batch_id=batch_id,
                    company_id=company_id,
                    status=final_status,
                    total_chunks=total_chunks,
                )

        except Exception as exc:
            log.error("rag_queue_worker_error", error=str(exc))
            await asyncio.sleep(1)


# ── Background chunk processor (legacy) ───────────────────────────────────


async def _process_rag_chunks(
    batch_id: str,
    company_id: str,
    chunks: list,
    total_rows: int,
) -> None:
    """Store each chunk to CRM with concurrency limit and retry.

    Failed chunks are written to Redis so they can be recovered manually.
    """
    sem = asyncio.Semaphore(_RAG_STORE_CONCURRENCY)
    total_chunks = len(chunks)
    failed_indices: list[int] = []

    async def _process_one(idx: int, chunk: object) -> None:
        async with sem:
            label = f"batch:{batch_id}:chunk:{idx + 1}/{total_chunks}"
            success = await _store_chunk_with_retry(company_id, chunk, label)
            if not success:
                failed_indices.append(idx)
                await _persist_failed_chunk(batch_id, company_id, idx, chunk)

    await asyncio.gather(*(_process_one(i, c) for i, c in enumerate(chunks)))

    if failed_indices:
        log.error(
            "rag_batch_partial_failure",
            batch_id=batch_id,
            company_id=company_id,
            total_chunks=total_chunks,
            failed_count=len(failed_indices),
            failed_indices=sorted(failed_indices),
        )
    else:
        log.info(
            "rag_batch_complete",
            batch_id=batch_id,
            company_id=company_id,
            total_chunks=total_chunks,
            total_rows=total_rows,
        )


async def _store_chunk_with_retry(
    company_id: str, chunk: object, label: str,
) -> bool:
    """Try up to ``_RAG_MAX_RETRIES`` times with exponential back-off."""
    for attempt in range(_RAG_MAX_RETRIES):
        # Records with record_id → bulk upsert per record
        if isinstance(chunk, list) and chunk and isinstance(chunk[0], dict) and chunk[0].get("record_id"):
            result = await bulk_store_webhook_data(company_id, chunk, label=label)
        else:
            result = await store_webhook_data(company_id, chunk, label=label)
        if result is not None:
            return True
        if attempt < _RAG_MAX_RETRIES - 1:
            delay = min(2 ** attempt, 30)
            log.warning(
                "rag_chunk_retry",
                label=label,
                attempt=attempt + 1,
                next_delay=delay,
            )
            await asyncio.sleep(delay)
    log.error("rag_chunk_exhausted_retries", label=label, company_id=company_id)
    return False


async def _persist_failed_chunk(
    batch_id: str, company_id: str, chunk_idx: int, chunk: object,
) -> None:
    """Write a failed chunk to Redis so it is never lost."""
    key = f"rag_failed:{company_id}:{batch_id}:chunk:{chunk_idx}"
    try:
        redis = get_redis()
        data = _json.dumps(
            {"company_id": company_id, "batch_id": batch_id, "chunk_idx": chunk_idx, "payload": chunk},
            ensure_ascii=False,
        )
        await redis.set(key, data, ex=_RAG_FAILED_TTL)
        log.info("rag_failed_chunk_persisted", redis_key=key, batch_id=batch_id, chunk_idx=chunk_idx)
    except Exception as exc:
        # Last resort: log the chunk size so ops can investigate
        log.critical(
            "rag_failed_chunk_redis_persist_error",
            batch_id=batch_id,
            chunk_idx=chunk_idx,
            error=str(exc),
            chunk_size=len(_json.dumps(chunk, ensure_ascii=False)) if chunk else 0,
        )


async def _upsert_session(
    pool, lead_db_id: str, company_id: str, session_id: str, score: int,
) -> None:
    """Chat path lead'inin session'ını qualifier_sessions tablosuna yaz.

    Aynı lead_id için zaten session varsa yeni oluşturmaz (duplicate webhook koruması).
    """
    try:
        existing = await pool.fetchval(
            "SELECT id FROM qualifier_sessions WHERE lead_id = $1::uuid LIMIT 1",
            lead_db_id,
        )
        if existing:
            log.debug("session_already_exists", lead_db_id=lead_db_id, existing_session=str(existing))
            return

        await pool.execute(
            """
            INSERT INTO qualifier_sessions (id, lead_id, company_id, score, stage, messages)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'PENDING', '[]'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            session_id, lead_db_id, company_id, score,
        )
    except Exception as exc:
        log.warning("session_db_write_failed", error=str(exc), session_id=session_id)
