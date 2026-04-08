"""
HandoffHandler:
  1. Enrich lead data (fill empty fields from CHAMP extraction)
  2. Generate reasoning_report via local LLM
  3. Send closing message via Groq
  4. Build signal summary + outreach mapping
  5. Send full HandoffPackage to CRM
  6. Set session.stage = HANDOFF
"""
import structlog
from typing import Any

from app.domain.conversation.session import SessionStage
from app.domain.conversation.prompts import build_handoff_closing_prompt
from app.domain.scoring.scorer import RuleBasedScorer
from app.domain.qualification.lead_enrichment import enrich_lead_from_extraction
from app.domain.qualification.outreach_mapping import (
    build_outreach_payload,
    build_signal_summary,
)
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.infrastructure.llm import local_llm_client
from app.infrastructure.llm.groq_client import complete_chat
from app.infrastructure.crm.webhook_client import send_to_crm
from app.infrastructure.crm.rest_client import (
    fetch_company_ai_config,
    fetch_company_fallback_url,
    find_or_create_customer,
    create_lead,
)
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

        # ── Fallback URL safety net: session'da yoksa company config'den çek ──
        if not fallback_url and session.company_id:
            try:
                fallback_url = await fetch_company_fallback_url(session.company_id)
                if fallback_url:
                    log.info("fallback_url_resolved_from_config",
                             session_id=session_id, company_id=session.company_id)
            except Exception as exc:
                log.warning("fallback_url_resolve_failed", error=str(exc))

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

        # ── WhatsApp lead qualify oldu — şimdi CRM'de customer + lead oluştur ─
        source = lead_json.get("source", "")
        contact = lead_json.get("contact", {})
        phone = contact.get("phone", "")
        if source == "whatsapp" and phone and session.company_id:
            try:
                customer = await find_or_create_customer(
                    company_id=session.company_id,
                    phone=phone,
                    name=contact.get("name") or phone,
                )
                if customer:
                    await create_lead(
                        company_id=session.company_id,
                        customer_id=customer["id"],
                        source="whatsapp",
                        extra_data={"channel": "whatsapp", "qualified_via": "chat_path"},
                    )
                    log.info("handoff_crm_customer_created", phone=phone, company_id=session.company_id)
            except Exception as exc:
                log.warning("handoff_crm_customer_failed", error=str(exc), phone=phone)

        # ── Enrich lead (fill empty fields from CHAMP/Judge) ────────────────
        msg_dicts = [
            {"role": m.role, "content": m.content, "ts": m.ts}
            for m in session.messages
        ]
        enriched_lead = enrich_lead_from_extraction(lead_json, champ_json, msg_dicts)

        # ── Build signal summary for sales team ──────────────────────────────
        # Get last composite result from score repo if available
        composite_breakdown: dict | None = None
        try:
            last_score_data = await self._score_repo.get_latest(session_id)
            if last_score_data and isinstance(last_score_data, dict):
                composite_breakdown = last_score_data.get("composite")
        except Exception:
            pass

        signal_summary = build_signal_summary(champ_json, composite_breakdown, msg_dicts)

        # ── Build outreach mapping (LeadOutreach-compatible) ─────────────────
        outreach = build_outreach_payload(
            session_id=session_id,
            lead_json=enriched_lead,
            score=score,
            champ_json=champ_json,
            reasoning_json=reasoning_json,
            messages=msg_dicts,
            composite_breakdown=composite_breakdown,
            signal_summary=signal_summary,
            handoff_path="chat",
        )

        # ── Send to CRM ──────────────────────────────────────────────────────
        pre_score = lead_json.get("initial_fit_score", 0)
        handoff = {
            "raw_payload": lead_json.get("raw_payload", {}),
            "lead": enriched_lead,
            "pre_score": pre_score,
            "qualified_score": score,
            "score": score,  # backward compat
            "reasoning_report": reasoning_json,
            "champ": champ_json,
            "session_id": session_id,
            "path": "chat",
            "conversation_transcript": [
                {"role": m.role, "content": m.content, "ts": m.ts}
                for m in session.messages
            ],
            "signal_summary": signal_summary,
            "composite_breakdown": composite_breakdown,
            "outreach": outreach,
        }

        success = await send_to_crm(handoff, self._session_repo, fallback_url=fallback_url)
        CRM_SEND_COUNTER.labels(path="chat", success=str(success)).inc()

        # Lead status → qualified + handoff record
        try:
            from app.infrastructure.db.pool import get_db_pool
            from app.infrastructure.db.lead_repo import insert_activity
            import json as _json
            pool = get_db_pool()
            lead_id_str = lead_json.get("id", "")
            cid = session.company_id
            if lead_id_str and cid:
                row = await pool.fetchrow(
                    "SELECT id, status FROM qualifier_leads WHERE company_id=$1 AND lead_id=$2",
                    cid, lead_id_str,
                )
                if row:
                    lead_db_id = str(row["id"])

                    # Update lead status
                    if row["status"] not in ("qualified", "won"):
                        await pool.execute(
                            "UPDATE qualifier_leads SET status='qualified', score=$1, updated_at=now() WHERE id=$2::uuid",
                            score, lead_db_id,
                        )
                        await insert_activity(
                            pool, company_id=cid, lead_db_id=lead_db_id,
                            event_type="status_change", actor="system",
                            payload={"old_status": row["status"], "new_status": "qualified", "reason": "handoff"},
                        )

                    # Write handoff record
                    await pool.execute(
                        """
                        INSERT INTO qualifier_handoffs
                          (session_id, lead_id, company_id, final_score, reasoning_json, champ_json, crm_sent, sent_at)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, now())
                        ON CONFLICT DO NOTHING
                        """,
                        session_id, lead_db_id, cid, score,
                        _json.dumps(reasoning_json) if reasoning_json else None,
                        _json.dumps(champ_json, ensure_ascii=False) if champ_json else None,
                        success,
                    )
                    log.info("handoff_db_recorded", lead_db_id=lead_db_id, score=score, crm_sent=success)
        except Exception as exc:
            log.warning("handoff_status_update_failed", error=str(exc))

        log.info("handoff_complete", session_id=session_id, score=score, crm_sent=success)
        return {"status": "handoff_complete", "session_id": session_id, "crm_sent": success}
