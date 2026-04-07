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
from app.infrastructure.crm.rest_client import fetch_company_ai_config
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
        if session.stage == SessionStage.HUMAN_TAKEOVER:
            return {"status": "skipped_human_takeover"}

        # ── Mark HANDOFF immediately (prevents double execution) ─────────────
        await self._session_repo.set_stage(session_id, SessionStage.HANDOFF)

        lead_json = session.lead_json
        score = session.score
        champ_json = session.champ_json
        fallback_url = session.fallback_url

        # ── Fetch company AI config ─────────────────────────────────────────
        company_config = None
        if session.company_id:
            try:
                company_config = await fetch_company_ai_config(session.company_id)
            except Exception as exc:
                log.warning("company_ai_config_fetch_failed_handoff", error=str(exc))

        # ── Generate reasoning report ────────────────────────────────────────
        reasoning_json: dict[str, Any] | None = None
        try:
            # Approximate breakdown from stored score
            breakdown = {"total": score, "note": "from_chat_path"}
            if champ_json:
                breakdown = {
                    "challenges": champ_json.get("challenges_score", 0),
                    "authority": champ_json.get("authority_score", 0),
                    "money": champ_json.get("money_score", 0),
                    "prioritization": champ_json.get("prioritization_score", 0),
                    "total": score,
                }
            result = await local_llm_client.generate_reasoning_report(
                lead_json, score, breakdown, champ_json,
                company_config=company_config,
            )
            reasoning_json = result.model_dump()
            # Enrich with judge analysis if available
            if champ_json and champ_json.get("holistic_reasoning"):
                reasoning_json["qualification_analysis"] = {
                    "holistic_score": champ_json.get("holistic_score"),
                    "holistic_reasoning": champ_json.get("holistic_reasoning"),
                    "icp_fit_assessment": champ_json.get("icp_fit_assessment"),
                    "negative_signals": champ_json.get("negative_signals", []),
                    "scoring_mode": champ_json.get("scoring_mode"),
                }
            # Save to session
            fresh = await self._session_repo.get(session_id)
            if fresh:
                fresh.reasoning_json = reasoning_json
                await self._session_repo.save(fresh)
        except Exception as exc:
            log.warning("reasoning_report_failed_handoff", session_id=session_id, error=str(exc))

        # ── Send closing message via Groq ────────────────────────────────────
        try:
            closing_prompt = build_handoff_closing_prompt(company_config=company_config)
            messages = [{"role": "system", "content": closing_prompt}]
            closing_msg = await complete_chat(messages)
            log.info("groq_closing_sent", session_id=session_id, msg=closing_msg[:80])
        except Exception as exc:
            log.warning("groq_closing_failed", session_id=session_id, error=str(exc))

        # ── Send to CRM ──────────────────────────────────────────────────────
        handoff = {
            "raw_payload": lead_json.get("raw_payload", {}),
            "lead": lead_json,
            "score": score,
            "reasoning_report": reasoning_json,
            "champ": champ_json,
            "session_id": session_id,
            "path": "chat",
        }

        success = await send_to_crm(handoff, self._session_repo, fallback_url=fallback_url)
        CRM_SEND_COUNTER.labels(path="chat", success=str(success)).inc()

        # Lead status → qualified
        try:
            from app.infrastructure.db.pool import get_db_pool
            from app.infrastructure.db.lead_repo import insert_activity
            pool = get_db_pool()
            lead_id_str = lead_json.get("id", "")
            cid = session.company_id
            if lead_id_str and cid:
                row = await pool.fetchrow(
                    "SELECT id, status FROM qualifier_leads WHERE company_id=$1 AND lead_id=$2",
                    cid, lead_id_str,
                )
                if row and row["status"] not in ("qualified", "won"):
                    lead_db_id = str(row["id"])
                    await pool.execute(
                        "UPDATE qualifier_leads SET status='qualified', score=$1, updated_at=now() WHERE id=$2::uuid",
                        score, lead_db_id,
                    )
                    await insert_activity(
                        pool, company_id=cid, lead_db_id=lead_db_id,
                        event_type="status_change", actor="system",
                        payload={"old_status": row["status"], "new_status": "qualified", "reason": "handoff"},
                    )
        except Exception as exc:
            log.warning("handoff_status_update_failed", error=str(exc))

        log.info("handoff_complete", session_id=session_id, score=score, crm_sent=success)
        return {"status": "handoff_complete", "session_id": session_id, "crm_sent": success}
