"""
Phase 7.B — HMAC-signed internal webhook client that pushes `pre_score.judged`
events directly to the CRM's /internal/webhooks/qualifier/pre-score listener.

Replaces the REST PATCH path (`try_update_ai_metadata`) with a realtime push.
During the dual-write rollout window both paths run; after observation the
REST flag is flipped off and this becomes the only write path.

Wire format:
    headers:
        X-Lisent-Signature: sha256=<hex>   # HMAC over `{ts_ms}.{body}`
        X-Lisent-Timestamp: <unix_ms>
        X-Lisent-Event-Id:  <uuid>          # mirrors body.event_id for idempotency
        Content-Type: application/json
    body:
        {
          "event_type": "pre_score.judged",
          "event_id":   "<uuid>",
          "tenant_id":  "<uuid>",
          "lead_id":    "<uuid>",            # qualifier lead_id
          "crm_lead_id":"<uuid>",            # CRM-side lead id (target of patch)
          "score":      <int>,
          "threshold":  <int>,
          "path":       "fast" | "chat",
          "timestamp":  "<iso8601>",
          "payload":    { ...ScoreEvent.payload verbatim... }
        }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

log = structlog.get_logger(__name__)


class CRMInternalWebhookClient:
    """Fire-and-retry HTTP client targeting the CRM's pre-score listener.

    Designed to live alongside RedisPubSubAdapter / WebhookFanoutAdapter as a
    third fan-out sink — the CRM is *not* a tenant (it's an internal consumer
    that ingests events from every tenant), so a tenant-scoped webhook
    subscription would be the wrong shape.

    Graceful semantics: any failure is logged and swallowed. The dual-write
    REST PATCH path is the fallback until the listener is proven reliable.
    """

    SIGNATURE_HEADER = "X-Lisent-Signature"
    TIMESTAMP_HEADER = "X-Lisent-Timestamp"
    EVENT_ID_HEADER = "X-Lisent-Event-Id"

    def __init__(
        self,
        *,
        url: str,
        secret: str,
        timeout: float = 5.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._secret = secret
        self._timeout = timeout

    def enabled(self) -> bool:
        return bool(self._url and self._secret)

    async def send_pre_score(
        self,
        *,
        tenant_id: str,
        lead_id: str,
        crm_lead_id: str,
        score: int,
        threshold: int,
        path: str,
        payload: dict[str, Any],
        event_id: str | None = None,
        timestamp_iso: str | None = None,
    ) -> bool:
        """POST pre_score.judged to the CRM. Returns True on 2xx, False otherwise.

        Args:
            crm_lead_id: target Lead.id on the CRM side — required. Empty
                string → we silently skip (no CRM mirror yet).
        """
        if not self.enabled():
            return False
        if not crm_lead_id:
            # Nothing to PATCH on the CRM side; skip rather than POST noise.
            return False

        evt_id = event_id or str(uuid.uuid4())
        envelope: dict[str, Any] = {
            "event_type": "pre_score.judged",
            "event_id": evt_id,
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "crm_lead_id": crm_lead_id,
            "score": int(score),
            "threshold": int(threshold),
            "path": path,
            "timestamp": timestamp_iso or "",
            "payload": payload,
        }
        body = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode(
            "utf-8",
        )
        ts_ms = int(time.time() * 1000)
        signature = self._sign(ts_ms, body)
        headers = {
            "Content-Type": "application/json",
            self.TIMESTAMP_HEADER: str(ts_ms),
            self.SIGNATURE_HEADER: signature,
            self.EVENT_ID_HEADER: evt_id,
            "User-Agent": "Lisent-Qualifier/1.0",
        }
        try:
            await self._post(body, headers)
            log.info(
                "crm_internal_webhook_sent",
                url=self._url,
                event_id=evt_id,
                crm_lead_id=crm_lead_id,
                score=score,
                path=path,
            )
            return True
        except Exception as exc:
            log.warning(
                "crm_internal_webhook_failed",
                url=self._url,
                event_id=evt_id,
                crm_lead_id=crm_lead_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return False

    def _sign(self, ts_ms: int, body: bytes) -> str:
        mac = hmac.new(
            self._secret.encode("utf-8"),
            msg=f"{ts_ms}.".encode("utf-8") + body,
            digestmod=hashlib.sha256,
        )
        return f"sha256={mac.hexdigest()}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _post(self, body: bytes, headers: dict[str, str]) -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)) as client:
            resp = await client.post(self._url, content=body, headers=headers)
            # 4xx is not retried (bad request / auth / unknown lead) — the
            # retry decorator only catches httpx.HTTPError which covers
            # transport + 5xx via raise_for_status below.
            if 400 <= resp.status_code < 500:
                log.warning(
                    "crm_internal_webhook_4xx",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                return
            resp.raise_for_status()


_CLIENT: CRMInternalWebhookClient | None = None


def get_crm_internal_webhook_client() -> CRMInternalWebhookClient:
    """Lazy singleton. Settings are read on first access."""
    global _CLIENT
    if _CLIENT is None:
        settings = get_settings()
        _CLIENT = CRMInternalWebhookClient(
            url=settings.crm_internal_webhook_url,
            secret=settings.crm_internal_webhook_secret,
            timeout=settings.crm_internal_webhook_timeout,
        )
    return _CLIENT


def reset_crm_internal_webhook_client() -> None:
    """Test hook — clears the cached singleton so updated env vars take effect."""
    global _CLIENT
    _CLIENT = None
