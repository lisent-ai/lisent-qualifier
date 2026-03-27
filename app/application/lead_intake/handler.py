"""
ProcessWebhookLeadHandler:
  score >= 80 → local LLM reasoning_report → CRM  (fast path)
  score  < 80 → create Redis session → ready for Groq chat
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
from app.domain.scoring.thresholds import HIGH_THRESHOLD
from app.domain.conversation.session import ConversationSession, SessionStage
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.infrastructure.crm.webhook_client import send_to_crm
from app.infrastructure.llm import local_llm_client
from app.application.lead_intake.commands import ProcessWebhookLeadCommand
from app.metrics import (
    LEADS_RECEIVED, LEADS_FAST_PATH, LEADS_CHAT_PATH,
    LEAD_SCORE_HISTOGRAM, CRM_SEND_COUNTER,
)

log = structlog.get_logger(__name__)

_scorer = RuleBasedScorer()


def _build_lead(data: dict[str, Any]) -> Lead:
    contact = ContactInfo(
        name=data.get("name", ""),
        phone=data.get("phone", ""),
        email=data.get("email", ""),
        city=data.get("city", ""),
    )
    return Lead(
        id=data.get("lead_id", str(uuid.uuid4())),
        source=LeadSource(data.get("source", LeadSource.OTHER)),
        contact=contact,
        project_type=ProjectType(data.get("project_type", ProjectType.OTHER)),
        budget_range=BudgetRange(data.get("budget_range", BudgetRange.UNKNOWN)),
        decision_authority=DecisionAuthority(data.get("decision_authority", DecisionAuthority.UNKNOWN)),
        timeline_urgency=TimelineUrgency(data.get("timeline_urgency", TimelineUrgency.UNKNOWN)),
        budget_amount=data.get("budget_amount"),
        notes=data.get("notes", ""),
        raw_payload=data,
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

        # ── Fast path: score >= 80 ───────────────────────────────────────────
        if score >= HIGH_THRESHOLD:
            LEADS_FAST_PATH.inc()
            return await self._fast_path(lead, score, breakdown, fallback_url=fallback_url)

        # ── Chat path: score < 80 ────────────────────────────────────────────
        LEADS_CHAT_PATH.inc()
        return await self._chat_path(lead, score, breakdown, fallback_url=fallback_url)

    async def _fast_path(
        self,
        lead: Lead,
        score: int,
        breakdown: dict[str, int],
        fallback_url: str | None = None,
    ) -> dict[str, Any]:
        lead_json = lead.to_dict()

        # Generate reasoning report (with fallback)
        reasoning_json: dict[str, Any] | None = None
        try:
            result = await local_llm_client.generate_reasoning_report(
                lead_json, score, breakdown
            )
            reasoning_json = result.model_dump()
        except Exception as exc:
            log.warning("reasoning_report_failed_fast_path", error=str(exc))

        handoff = {
            "lead": lead_json,
            "score": score,
            "score_breakdown": breakdown,
            "reasoning_report": reasoning_json,
            "bant": None,
            "session_id": None,
            "path": "fast",
        }

        success = await send_to_crm(handoff, self._session_repo, fallback_url=fallback_url)
        CRM_SEND_COUNTER.labels(path="fast", success=str(success)).inc()

        log.info("fast_path_complete", lead_id=lead.id, score=score, crm_sent=success)
        return {"status": "fast_path", "lead_id": lead.id, "score": score, "crm_sent": success}

    async def _chat_path(
        self,
        lead: Lead,
        score: int,
        breakdown: dict[str, int],
        fallback_url: str | None = None,
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        session = ConversationSession(
            session_id=session_id,
            lead_json=lead.to_dict(),
            score=score,
            stage=SessionStage.CHAT,
            fallback_url=fallback_url,
        )
        await self._session_repo.save(session)
        await self._score_repo.record(session_id, score)

        log.info("chat_session_created", lead_id=lead.id, session_id=session_id, score=score)
        return {
            "status": "chat_path",
            "lead_id": lead.id,
            "session_id": session_id,
            "score": score,
        }
