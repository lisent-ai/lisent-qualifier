"""
BANT extraction BackgroundTask.
Acquires Redis lock → calls local LLM → validates with Pydantic → updates session score.
If score crosses HIGH_THRESHOLD, triggers handoff.
"""
import structlog

from app.config import get_settings
from app.domain.scoring.thresholds import HIGH_THRESHOLD
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.infrastructure.llm import local_llm_client
from app.metrics import BANT_EXTRACTIONS, HANDOFF_COUNTER

log = structlog.get_logger(__name__)


async def extract_bant_task(
    session_id: str,
    session_repo: SessionRepository,
    score_repo: ScoreRepository,
) -> None:
    """
    Idempotent BANT extraction task.
    Safe to run concurrently — Redis lock prevents double execution.
    """
    # ── Acquire dedup lock ───────────────────────────────────────────────────
    acquired = await session_repo.acquire_bant_lock(session_id)
    if not acquired:
        log.debug("bant_lock_already_held", session_id=session_id)
        return

    try:
        session = await session_repo.get(session_id)
        if session is None:
            log.warning("bant_session_not_found", session_id=session_id)
            return

        if session.stage.value == "HANDOFF":
            log.debug("bant_skip_already_handoff", session_id=session_id)
            return

        # Build conversation text
        conversation_text = "\n".join(
            f"{m.role.upper()}: {m.content}" for m in session.messages
        )
        if not conversation_text.strip():
            return

        # ── Call local LLM ───────────────────────────────────────────────────
        try:
            bant_result = await local_llm_client.extract_bant(conversation_text)
            BANT_EXTRACTIONS.labels(success="true").inc()
        except Exception as exc:
            log.warning("bant_extraction_failed", session_id=session_id, error=str(exc))
            BANT_EXTRACTIONS.labels(success="false").inc()
            return

        bant_json = bant_result.model_dump()
        bant_total = bant_result.total

        # Combine with rule-based score (take max to avoid regression)
        new_score = max(session.score, bant_total)
        await session_repo.update_bant(session_id, bant_json, new_score)
        await score_repo.record(session_id, new_score)

        log.info(
            "bant_extracted",
            session_id=session_id,
            bant_total=bant_total,
            old_score=session.score,
            new_score=new_score,
        )

        # ── Publish score update via Redis PubSub ────────────────────────────
        import json
        from app.infrastructure.redis.client import get_redis
        redis = get_redis()
        await redis.publish(
            f"score:{session_id}",
            json.dumps({"session_id": session_id, "score": new_score, "bant": bant_json}),
        )

        # ── Handoff check ────────────────────────────────────────────────────
        if new_score >= HIGH_THRESHOLD:
            log.info("handoff_threshold_reached", session_id=session_id, score=new_score)
            HANDOFF_COUNTER.labels(path="chat").inc()
            from app.application.qualification.handler import HandoffHandler
            from app.infrastructure.crm.webhook_client import send_to_crm
            handler = HandoffHandler(session_repo, score_repo)
            await handler.handle(session_id)

    finally:
        await session_repo.release_bant_lock(session_id)
