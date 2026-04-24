"""ProcessWebhookLeadHandler — thin intake gate.

Phase 7 (2026-04-24): Legacy sync scoring removed. The handler now only
performs idempotency dedup; all downstream work (DB INSERT, pre-score
enqueue, CRM writeback, event publish) happens in the router + PreScoreWorker.

Responsibilities:
    1. Increment intake counter.
    2. Atomic dedup via Redis `dedup:{lead_id}` (SET NX + TTL). If duplicate,
       return early with {"status": "duplicate"}; router skips DB work.
    3. Otherwise return {"status": "accepted"}; router proceeds with
       INSERT qualifier_leads (score=0) + enqueue_lead.

Chat path + WhatsApp greeting + CRM fast-path handoff are NOT invoked from
here anymore — they are future work (Phase 8+) to be rebuilt on top of the
new async pipeline, if needed. The greeting_worker module is still present
but its queue is dormant until a future re-integration.
"""
from __future__ import annotations

from typing import Any

import structlog

from app.application.lead_intake.commands import ProcessWebhookLeadCommand
from app.infrastructure.redis.score_repo import ScoreRepository
from app.infrastructure.redis.session_repo import SessionRepository
from app.metrics import LEADS_RECEIVED

log = structlog.get_logger(__name__)


class ProcessWebhookLeadHandler:
    """Thin intake handler — dedup only.

    Kept as a class (vs. a bare function) so the DI wiring in
    `app/api/deps.py:get_lead_intake_handler` stays unchanged.
    """

    def __init__(
        self,
        session_repo: SessionRepository,
        score_repo: ScoreRepository,
    ) -> None:
        self._session_repo = session_repo
        # score_repo kept for DI signature compatibility; no longer used here.
        self._score_repo = score_repo

    async def handle(self, cmd: ProcessWebhookLeadCommand) -> dict[str, Any]:
        LEADS_RECEIVED.inc()

        data = cmd.lead_data
        lead_id = data.get("lead_id", "")

        # Dedup key: prefer CRM-supplied external_lead_id (unique CRM row id)
        # over sender-side lead_id (can collide across CRM intranet forwards).
        dedup_key = cmd.external_lead_id or lead_id
        if dedup_key and await self._session_repo.is_duplicate_lead(dedup_key):
            log.info(
                "duplicate_lead_ignored",
                dedup_key=dedup_key,
                lead_id=lead_id,
            )
            return {"status": "duplicate", "lead_id": lead_id}

        return {"status": "accepted", "lead_id": lead_id}
