"""
GenericWebhookAdapter — External tenant'lar için HMAC-signed webhook POST.

`tenant.outbound_webhook_url` ve `tenant.outbound_webhook_secret` kullanılır.
İmzalama: HMAC-SHA256 over raw request body, `X-Lisent-Signature` header'ında.
Replay protection için `t={unix_ts},v1={hex}` formatı (Stripe tarzı).

Retry + circuit breaker aynı pattern'i kullanır (tenacity + circuitbreaker).

Phase 1.D.2: iskele + wrap. HMAC + retry + outbox Phase 1.E'de application
layer'dan çağrılarak entegre edilecek.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from circuitbreaker import circuit
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.ports.handoff import HandoffPayload, HandoffPort, HandoffResult

log = structlog.get_logger(__name__)


class GenericWebhookAdapter(HandoffPort):
    """HandoffPort that POSTs HMAC-signed JSON to tenant-configured webhook URL."""

    def __init__(
        self,
        webhook_url: str,
        webhook_secret: str,
        session_repo: Any = None,
    ) -> None:
        self._url = webhook_url
        self._secret = webhook_secret
        self._session_repo = session_repo

    @property
    def name(self) -> str:
        return "generic_webhook"

    async def send(self, payload: HandoffPayload) -> HandoffResult:
        """HMAC-signed POST to configured URL."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        if not self._url:
            return HandoffResult(
                success=False,
                adapter_name=self.name,
                attempted_at=now_iso,
                error="webhook_url not configured",
            )

        body = self._serialize_payload(payload)
        signature = self._sign(body)

        try:
            await self._post(body, signature)
            return HandoffResult(
                success=True,
                adapter_name=self.name,
                attempted_at=now_iso,
            )
        except Exception as exc:
            log.error("generic_webhook_failed", url=self._url, error=str(exc))

            # Outbox retry — tenant'ın kendi webhook'u için de aynı Redis queue
            retry_queued = False
            if self._session_repo is not None:
                envelope = {
                    "webhook_url": self._url,
                    "signature": signature,
                    "body": body.decode("utf-8"),
                    "adapter": self.name,
                }
                try:
                    await self._session_repo.push_crm_outbox(envelope)
                    retry_queued = True
                    log.info("generic_webhook_outbox_queued", url=self._url)
                except Exception as outbox_exc:  # pragma: no cover
                    log.error("generic_webhook_outbox_push_failed", error=str(outbox_exc))

            return HandoffResult(
                success=False,
                adapter_name=self.name,
                attempted_at=now_iso,
                error=str(exc),
                retry_queued=retry_queued,
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_payload(payload: HandoffPayload) -> bytes:
        """HandoffPayload → deterministic JSON bytes (sorted keys for stable HMAC)."""
        doc: dict[str, Any] = {
            "lead_id": str(payload.lead_id),
            "tenant_id": str(payload.tenant_id),
            "session_id": str(payload.session_id) if payload.session_id else None,
            "external_ref": payload.external_ref,
            "score": payload.score,
            "threshold": payload.threshold,
            "status": payload.status,
            "path": payload.path,
            "framework": payload.framework,
            "contact": payload.contact,
            "champ": payload.champ,
            "reasoning": payload.reasoning,
            "score_breakdown": payload.score_breakdown,
            "recommendation": payload.recommendation,
            "cta_type": payload.cta_type,
            "meeting_url": payload.meeting_url,
        }
        return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _sign(self, body: bytes) -> str:
        """Stripe-style HMAC signature: `t=<unix_ts>,v1=<hex>`."""
        ts = int(time.time())
        signed_payload = f"{ts}.".encode() + body
        sig = hmac.new(self._secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        return f"t={ts},v1={sig}"

    @circuit(failure_threshold=3, recovery_timeout=120, expected_exception=Exception)
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _post(self, body: bytes, signature: str) -> None:
        settings = get_settings()
        timeout = httpx.Timeout(settings.crm_webhook_timeout)
        headers = {
            "Content-Type": "application/json",
            "X-Lisent-Signature": signature,
            "User-Agent": "Lisent-Webhook/1.0",
        }
        async with httpx.AsyncClient(timeout=timeout, http2=True) as client:
            response = await client.post(self._url, content=body, headers=headers)
            response.raise_for_status()
            log.info(
                "generic_webhook_sent",
                url=self._url,
                status=response.status_code,
            )
