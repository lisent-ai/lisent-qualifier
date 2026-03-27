"""
HandoffHandler:
  1. Generate reasoning_report via local LLM
  2. Send closing message via Groq
  3. Send full HandoffPackage to CRM
  4. Set session.stage = HANDOFF
"""
import structlog
from typing import Any

from app.domain.conversation.session import SessionStage
from app.domain.conversation.prompts import build_handoff_closing_prompt
from app.domain.scoring.scorer import RuleBasedScorer
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.infrastructure.llm import local_llm_client
from app.infrastructure.llm.groq_client import complete_chat
from app.infrastructure.crm.webhook_client import send_to_crm
from app.metrics import CRM_SEND_COUNTER

log = structlog.get_logger(__name__)

_scorer = RuleBasedScorer()


class HandoffHandler:
    def __init__(
        self,
        session_repo: SessionRepository,
        score_repo: ScoreRepository,
    ) -> None:
        self._session_repo = session_repo
        self._score_repo = score_repo

    async def handle(self, session_id: str) -> dict[str, Any]:
        session = await self._session_repo.get(session_id)
        if session is None:
            return {"error": "session_not_found"}
        if session.stage == SessionStage.HANDOFF:
            return {"status": "already_handoff"}

        # ── Mark HANDOFF immediately (prevents double execution) ─────────────
        await self._session_repo.set_stage(session_id, SessionStage.HANDOFF)

        lead_json = session.lead_json
        score = session.score
        bant_json = session.bant_json
        fallback_url = session.fallback_url

        # ── Generate reasoning report ────────────────────────────────────────
        reasoning_json: dict[str, Any] | None = None
        try:
            # Approximate breakdown from stored score (lead entity may not be available)
            breakdown = {"total": score, "note": "from_chat_path"}
            if bant_json:
                breakdown = {
                    "budget": bant_json.get("budget_score", 0),
                    "authority": bant_json.get("authority_score", 0),
                    "need": bant_json.get("need_score", 0),
                    "timeline": bant_json.get("timeline_score", 0),
                    "total": score,
                }
            result = await local_llm_client.generate_reasoning_report(
                lead_json, score, breakdown, bant_json
            )
            reasoning_json = result.model_dump()
            # Save to session
            fresh = await self._session_repo.get(session_id)
            if fresh:
                fresh.reasoning_json = reasoning_json
                await self._session_repo.save(fresh)
        except Exception as exc:
            log.warning("reasoning_report_failed_handoff", session_id=session_id, error=str(exc))

        # ── Send closing message via Groq ────────────────────────────────────
        try:
            closing_prompt = build_handoff_closing_prompt()
            messages = [{"role": "system", "content": closing_prompt}]
            closing_msg = await complete_chat(messages)
            log.info("groq_closing_sent", session_id=session_id, msg=closing_msg[:80])
        except Exception as exc:
            log.warning("groq_closing_failed", session_id=session_id, error=str(exc))

        # ── Send to CRM ──────────────────────────────────────────────────────
        handoff = {
            "lead": lead_json,
            "score": score,
            "reasoning_report": reasoning_json,
            "bant": bant_json,
            "session_id": session_id,
            "path": "chat",
        }

        success = await send_to_crm(handoff, self._session_repo, fallback_url=fallback_url)
        CRM_SEND_COUNTER.labels(path="chat", success=str(success)).inc()

        log.info("handoff_complete", session_id=session_id, score=score, crm_sent=success)
        return {"status": "handoff_complete", "session_id": session_id, "crm_sent": success}
