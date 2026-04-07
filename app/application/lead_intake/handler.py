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

        # ── Fast path: score >= threshold ────────────────────────────────────
        if score >= threshold:
            LEADS_FAST_PATH.inc()
            return await self._fast_path(
                lead, score, breakdown,
                fallback_url=fallback_url,
                company_config=company_config,
            )

        # ── Chat path: score < threshold → PENDING (AI bekler) ──────────────
        LEADS_CHAT_PATH.inc()
        return await self._chat_path(lead, score, breakdown, fallback_url=fallback_url, company_id=cmd.company_id)

    async def _fast_path(
        self,
        lead: Lead,
        score: int,
        breakdown: dict[str, int],
        fallback_url: str | None = None,
        company_config: dict[str, Any] | None = None,
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
        }

    async def _chat_path(
        self,
        lead: Lead,
        score: int,
        breakdown: dict[str, int],
        fallback_url: str | None = None,
        company_id: str = "",
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        lead_dict = lead.to_dict()
        lead_dict["initial_fit_score"] = score  # preserve pre_score for handoff
        session = ConversationSession(
            session_id=session_id,
            lead_json=lead_dict,
            score=score,
            stage=SessionStage.PENDING,  # AI bekler, kullanıcı başlatır
            fallback_url=fallback_url,
            company_id=company_id,
        )
        await self._session_repo.save(session)
        await self._score_repo.record(session_id, score)

        log.info("chat_session_created", lead_id=lead.id, session_id=session_id,
                 score=score, stage="PENDING")

        return {
            "status": "chat_path",
            "lead_id": lead.id,
            "session_id": session_id,
            "pre_score": score,
            "score": score,  # backward compat
        }
