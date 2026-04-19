"""
ProcessWebhookLeadHandler:
  score >= threshold → local LLM reasoning_report → CRM  (fast path)
  score  < threshold → create Redis session (PENDING) → wait for user to start AI
"""
import uuid
import structlog
from typing import Any

from app.config import get_settings
from app.domain.lead.entities import Lead, ContactInfo
from app.domain.lead.enums import (
    LeadSource, ProjectType, BudgetRange, DecisionAuthority, TimelineUrgency,
)
from app.domain.scoring.scorer import RuleBasedScorer
from app.domain.scoring.thresholds import HIGH_THRESHOLD, compute_threshold
from app.domain.conversation.session import ConversationSession, SessionStage
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.infrastructure.crm.webhook_client import send_to_crm
from app.infrastructure.crm.rest_client import fetch_company_ai_config
from app.infrastructure.llm import local_llm_client
from app.application.crm_sync import (
    try_create_or_upsert_crm_lead,
    try_update_ai_metadata,
)
from app.application.lead_intake.commands import ProcessWebhookLeadCommand
from app.metrics import (
    LEADS_RECEIVED, LEADS_FAST_PATH, LEADS_CHAT_PATH,
    LEAD_SCORE_HISTOGRAM, CRM_SEND_COUNTER,
)

log = structlog.get_logger(__name__)

_scorer = RuleBasedScorer()


def _safe(enum_cls, value, default):
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return default


def _build_lead(data: dict[str, Any]) -> Lead:
    extra = data.get("extra_data", {})

    def get(key: str, default: Any = "") -> Any:
        return data.get(key) or extra.get(key) or default

    contact = ContactInfo(
        name=get("name"),
        phone=get("phone"),
        email=get("email"),
        city=get("city"),
    )
    return Lead(
        id=get("lead_id") or str(uuid.uuid4()),
        source=_safe(LeadSource, get("source"), LeadSource.OTHER),
        contact=contact,
        project_type=_safe(ProjectType, get("project_type"), ProjectType.OTHER),
        budget_range=_safe(BudgetRange, get("budget_range"), BudgetRange.UNKNOWN),
        decision_authority=_safe(DecisionAuthority, get("decision_authority"), DecisionAuthority.UNKNOWN),
        timeline_urgency=_safe(TimelineUrgency, get("timeline_urgency"), TimelineUrgency.UNKNOWN),
        budget_amount=get("budget_amount", None),
        notes=get("notes"),
        raw_payload=data.get("raw_payload", data),
    )


class ProcessWebhookLeadHandler:
    def __init__(
        self,
        session_repo: SessionRepository,
        score_repo: ScoreRepository,
    ) -> None:
        self._session_repo = session_repo
        self._score_repo = score_repo

    async def handle(self, cmd: ProcessWebhookLeadCommand) -> dict[str, Any]:
        data = cmd.lead_data
        LEADS_RECEIVED.inc()

        # ── Idempotency check ────────────────────────────────────────────────
        lead_id = data.get("lead_id", "")
        if lead_id and await self._session_repo.is_duplicate_lead(lead_id):
            log.info("duplicate_lead_ignored", lead_id=lead_id)
            return {"status": "duplicate", "lead_id": lead_id}

        # ── Build domain entity & score ──────────────────────────────────────
        lead = _build_lead(data)
        score = _scorer.compute(lead)
        breakdown = _scorer.breakdown(lead)

        LEAD_SCORE_HISTOGRAM.observe(score)
        log.info("lead_scored", lead_id=lead.id, score=score)

        fallback_url: str | None = cmd.fallback_url

        # ── Fetch company AI config ─────────────────────────────────────────
        company_config = None
        if cmd.company_id:
            try:
                company_config = await fetch_company_ai_config(cmd.company_id)
            except Exception as exc:
                log.warning("company_ai_config_fetch_failed_intake", error=str(exc))

        # ── Dynamic threshold based on project type + budget ─────────────────
        threshold = compute_threshold(
            project_type=lead.project_type.value,
            budget_range=lead.budget_range.value,
            company_config=company_config,
        )

        # ── CRM write-through: create lead mirror in CRM (idempotent) ───────
        crm_lead_id = await try_create_or_upsert_crm_lead(
            company_id=cmd.company_id,
            lead=lead,
            source_override="ai_qualifier",
        )
        initial_ai_status = "qualified" if score >= threshold else "chatting"
        initial_path = "fast" if score >= threshold else "chat"
        await try_update_ai_metadata(
            crm_lead_id or "",
            score=score,
            status=initial_ai_status,
            score_breakdown=breakdown,
            path=initial_path,
            idempotency_key=f"lead-initial-score-{lead.id}",
        )

        # ── Fast path: score >= threshold ────────────────────────────────────
        if score >= threshold:
            LEADS_FAST_PATH.inc()
            return await self._fast_path(
                lead, score, breakdown,
                fallback_url=fallback_url,
                company_config=company_config,
                crm_lead_id=crm_lead_id,
            )

        # ── Chat path: score < threshold → PENDING (AI bekler) ──────────────
        LEADS_CHAT_PATH.inc()
        return await self._chat_path(
            lead, score, breakdown,
            fallback_url=fallback_url,
            company_id=cmd.company_id,
            crm_lead_id=crm_lead_id,
        )

    async def _fast_path(
        self,
        lead: Lead,
        score: int,
        breakdown: dict[str, int],
        fallback_url: str | None = None,
        company_config: dict[str, Any] | None = None,
        crm_lead_id: str | None = None,
    ) -> dict[str, Any]:
        lead_json = lead.to_dict()

        # Generate reasoning report (with fallback)
        reasoning_json: dict[str, Any] | None = None
        try:
            result = await local_llm_client.generate_reasoning_report(
                lead_json, score, breakdown,
                company_config=company_config,
            )
            reasoning_json = result.model_dump()
        except Exception as exc:
            log.warning("reasoning_report_failed_fast_path", error=str(exc))

        # CRM write-through: final qualification + reasoning (no session for fast path)
        await try_update_ai_metadata(
            crm_lead_id or "",
            status="qualified",
            reasoning=reasoning_json,
            idempotency_key=f"lead-fast-handoff-{lead.id}",
        )

        handoff = {
            "raw_payload": lead.raw_payload,
            "lead": lead_json,
            "pre_score": score,
            "qualified_score": score,  # fast path: pre = qualified (skipped chat)
            "score": score,  # backward compat
            "reasoning_report": reasoning_json,
            "champ": None,
            "session_id": None,
            "path": "fast",
            "crm_lead_id": crm_lead_id,
        }

        success = await send_to_crm(handoff, self._session_repo, fallback_url=fallback_url)
        CRM_SEND_COUNTER.labels(path="fast", success=str(success)).inc()

        log.info("fast_path_complete", lead_id=lead.id, score=score, crm_sent=success)
        return {
            "status": "fast_path",
            "lead_id": lead.id,
            "pre_score": score,
            "qualified_score": score,
            "score": score,  # backward compat
            "crm_sent": success,
            "crm_lead_id": crm_lead_id,
        }

    async def _chat_path(
        self,
        lead: Lead,
        score: int,
        breakdown: dict[str, int],
        fallback_url: str | None = None,
        company_id: str = "",
        crm_lead_id: str | None = None,
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        lead_dict = lead.to_dict()
        lead_dict["initial_fit_score"] = score  # preserve pre_score for handoff
        # Form-sourced chat leads with a phone number kick off proactive
        # WhatsApp outreach when the company has Green API connected. This
        # makes the "AI Lead Qualifier channels" promise real — the user
        #receives the opening message on WhatsApp instead of having to
        #find a browser-side chat UI. If Green API is not configured the
        #greeting worker silently skips (logs "greeting_no_whatsapp"),
        #so the form path keeps working end-to-end either way.
        phone = (lead.contact.phone or "").strip()
        clean_phone = _normalize_phone(phone)
        has_whatsapp_channel = bool(company_id and clean_phone)

        # Stage choice:
        #   * WhatsApp outreach queued → CHAT immediately (the greeting worker
        #    will stream an AI opener and send it on WhatsApp).
        #  * No WhatsApp channel → PENDING; the session still exists for
        #    internal browser / API-based chat flows.
        initial_stage = SessionStage.CHAT if has_whatsapp_channel else SessionStage.PENDING

        session = ConversationSession(
            session_id=session_id,
            lead_json=lead_dict,
            score=score,
            stage=initial_stage,
            fallback_url=fallback_url,
            company_id=company_id,
            crm_lead_id=crm_lead_id or "",
        )
        await self._session_repo.save(session)
        await self._score_repo.record(session_id, score)

        # CRM write-through: link session id to the CRM lead
        await try_update_ai_metadata(
            crm_lead_id or "",
            session_id=session_id,
            status="chatting",
            idempotency_key=f"lead-chat-session-bind-{lead.id}",
        )

        # Enqueue the WhatsApp greeting. The background greeting_worker
        # picks it up, streams an AI opener, sends it via Green API, and
        #binds phone→session so the user's reply lands on the same session.
        greeting_queued = False
        if has_whatsapp_channel:
            try:
                await self._session_repo.push_wa_greeting(
                    company_id,
                    {
                        "session_id": session_id,
                        "company_id": company_id,
                        "phone": clean_phone,
                        "lead_id": lead.id,
                        "crm_lead_id": crm_lead_id or "",
                    },
                )
                greeting_queued = True
            except Exception as exc:
                log.warning(
                    "chat_path_greeting_queue_failed",
                    session_id=session_id,
                    error=str(exc),
                )

        log.info(
            "chat_session_created",
            lead_id=lead.id,
            session_id=session_id,
            score=score,
            stage=str(initial_stage),
            whatsapp_greeting_queued=greeting_queued,
            phone_present=bool(clean_phone),
        )

        return {
            "status": "chat_path",
            "lead_id": lead.id,
            "session_id": session_id,
            "pre_score": score,
            "score": score,  # backward compat
            "crm_lead_id": crm_lead_id,
            "whatsapp_greeting_queued": greeting_queued,
        }


def _normalize_phone(phone: str) -> str:
    """Strip to digits so WhatsApp chatId (NNNN@c.us) builds reliably."""
    if not phone:
        return ""
    return "".join(ch for ch in phone if ch.isdigit())
