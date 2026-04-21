"""
LisentCRMHandoffAdapter — Mevcut CRM webhook gönderimini HandoffPort'a uydurur.

Mevcut `app.infrastructure.crm.webhook_client.send_to_crm()` fonksiyonunu sarar.
Circuit breaker + tenacity retry + Redis dead-letter outbox davranışı aynen korunur.

Fallback URL stratejisi:
    1. `tenant.outbound_webhook_url` (qualifier DB'deki legacy tenant için CRM
       fallback_url'inin eşdeğeri — migration bunu set etmiş olabilir)
    2. Global `settings.crm_webhook_url` (env var fallback)
    3. Hiçbiri yoksa: sessizce skip (`success=False`)

Outbox:
    Fail durumunda session_repo'ya envelope push (dead-letter). Background worker
    (`app.main._outbox_flusher`) 60 saniyede bir flush eder.

Phase 1.D.2: adapter iskele + wrap. Session_repo injection gerekli. Phase 1.E'de
application handler'ları bu adapter'a switch edilecek.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from dataclasses import asdict

import structlog

from app.infrastructure.crm import webhook_client as crm_webhook_client
from app.ports.handoff import HandoffPayload, HandoffPort, HandoffResult

if TYPE_CHECKING:
    pass  # session_repo tipi lazy/duck-typed

log = structlog.get_logger(__name__)


class LisentCRMHandoffAdapter(HandoffPort):
    """HandoffPort over mevcut CRM webhook client."""

    def __init__(
        self,
        session_repo: Any = None,  # CRM outbox için, None ise no dead-letter
        fallback_url_override: str | None = None,
    ) -> None:
        self._session_repo = session_repo
        self._fallback_url_override = fallback_url_override

    @property
    def name(self) -> str:
        return "lisent_crm"

    async def send(self, payload: HandoffPayload) -> HandoffResult:
        """Qualified lead'i CRM'e teslim et. Retry + circuit breaker otomatik."""
        now_iso = datetime.now(timezone.utc).isoformat()

        # HandoffPayload dataclass → dict (CRM'in beklediği format)
        crm_payload: dict[str, Any] = self._to_crm_payload(payload)

        # Fallback URL — tenant config'ten geliyor (tenant port tarafından set edilmiş)
        fallback_url = self._fallback_url_override or payload.raw_payload.get("fallback_url")

        success = await crm_webhook_client.send_to_crm(
            payload=crm_payload,
            session_repo=self._session_repo,
            fallback_url=fallback_url,
        )

        if success:
            return HandoffResult(
                success=True,
                adapter_name=self.name,
                attempted_at=now_iso,
            )

        # Fail: session_repo'ya outbox'a push olmuş olabilir
        return HandoffResult(
            success=False,
            adapter_name=self.name,
            attempted_at=now_iso,
            error="crm webhook failed (see logs)",
            retry_queued=self._session_repo is not None,
        )

    @staticmethod
    def _to_crm_payload(payload: HandoffPayload) -> dict[str, Any]:
        """HandoffPayload → CRM webhook_client beklediği sözlük formatı.

        Mevcut CRM webhook schema'sı (legacy):
            {
                "lead": {...},
                "score": 78,
                "qualified_score": 78,
                "session_id": "...",
                "path": "chat" | "fast",
                "champ": {...},
                "reasoning_report": {...},
                "raw_payload": {...}
            }
        """
        return {
            "lead": {
                "id": str(payload.lead_id),
                "external_ref": payload.external_ref,
                "contact": payload.contact,
            },
            "score": payload.score,
            "qualified_score": payload.score,
            "threshold": payload.threshold,
            "status": payload.status,
            "session_id": str(payload.session_id) if payload.session_id else None,
            "path": payload.path,
            "framework": payload.framework,
            "champ": payload.champ,
            "reasoning_report": payload.reasoning,
            "score_breakdown": payload.score_breakdown,
            "recommendation": payload.recommendation,
            "cta_type": payload.cta_type,
            "meeting_url": payload.meeting_url,
            "raw_payload": payload.raw_payload,
        }
