"""Outbound webhook fanout — allowlist + payload-mode contract.

Outbound surface covers all pipeline status events (`lead.stage_changed`,
`lead.qualified`, `lead.won`, `lead.lost`, `lead.disqualified`). Body
shape depends on the tenant's `outbound_webhook_payload_mode`:
  * minimal → `{event_type, external_id}`
  * full    → adds tenant_id/lead_id/from_stage/to_stage/lead snapshot
external_id falls back to lead_id when no partner upstream id exists,
so native CRM leads still trigger webhooks. Pre-pipeline events
(`score.*`, `pre_score.*`, lifecycle) stay internal.

Loop-prevention tests for `deliver()` (header extraction from body) are
kept — `deliver` is a generic helper that may still see legacy bodies
during a deploy window or from other publishers.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx

from app.adapters.event.webhook_fanout import (
    OUTBOUND_EVENT_ALLOWLIST,
    WebhookFanoutAdapter,
)
from app.application.webhook.delivery import _extract_origin_headers, deliver
from app.ports.event import ScoreEvent

# ──────────────────────────────────────────────── deliver() header extraction


def test_extract_origin_headers_from_top_level():
    body = json.dumps({
        "external_id": "A-999",
        "origin_system": "partner_intranet",
        "event_type": "score.updated",
    }).encode()
    assert _extract_origin_headers(body) == ("partner_intranet", "A-999")


def test_extract_origin_headers_from_payload_fallback():
    body = json.dumps({
        "event_type": "score.updated",
        "payload": {"external_id": "B-42", "origin_system": "partner_intranet"},
    }).encode()
    assert _extract_origin_headers(body) == ("partner_intranet", "B-42")


def test_extract_origin_headers_missing_returns_none():
    assert _extract_origin_headers(b'{"event_type":"score.updated"}') == (None, None)


def test_extract_origin_headers_handles_garbage_body():
    assert _extract_origin_headers(b"not json") == (None, None)


def _collect_headers(captured: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})
    return handler


def test_deliver_adds_loop_prevention_headers():
    captured: dict = {}
    transport = httpx.MockTransport(_collect_headers(captured))
    client = httpx.AsyncClient(transport=transport)

    body = json.dumps({
        "external_id": "A-999",
        "origin_system": "partner_intranet",
    }).encode()

    outcome = asyncio.run(deliver(
        url="https://partner.example.com/hook",
        secret="topsecret",
        body=body,
        event_id="123",
        event_type="score.updated",
        delivery_id="d-1",
        tenant_id="t-1",
        client=client,
    ))
    asyncio.run(client.aclose())

    assert outcome.ok is True
    assert captured["headers"]["x-lisent-origin"] == "partner_intranet"
    assert captured["headers"]["x-lisent-external-id"] == "A-999"
    assert "x-lisent-signature" in captured["headers"]


def test_deliver_omits_headers_when_body_has_no_origin():
    captured: dict = {}
    transport = httpx.MockTransport(_collect_headers(captured))
    client = httpx.AsyncClient(transport=transport)

    asyncio.run(deliver(
        url="https://partner.example.com/hook",
        secret="s",
        body=b'{"event_type":"score.updated"}',
        event_id="1", event_type="score.updated",
        delivery_id="d", tenant_id="t",
        client=client,
    ))
    asyncio.run(client.aclose())

    assert "x-lisent-origin" not in captured["headers"]
    assert "x-lisent-external-id" not in captured["headers"]


# ──────────────────────────────────────────────── fanout allowlist contract


def _make_adapter_with_config(enabled_events=None, payload_mode="minimal"):
    """Adapter wired with an in-memory tenant config cache so publish() skips DB."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps({
        "url": "https://partner.example.com/hook",
        "secret": "s",
        "enabled_events": enabled_events or ["*"],
        "payload_mode": payload_mode,
    }))
    redis.rpush = AsyncMock()
    redis.setex = AsyncMock()
    pool = MagicMock()
    return WebhookFanoutAdapter(redis=redis, db_pool=pool), redis


def _event(event_type: str, *, external_id: str | None = "A-999",
           origin: str | None = "partner_intranet"):
    return ScoreEvent(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        lead_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id=None,
        event_type=event_type,
        score=80,
        threshold=75,
        path="fast",
        payload={},
        timestamp=datetime(2026, 4, 24, 12, 0, 0),
        external_id=external_id,
        origin_system=origin,
        source="intranet" if origin else None,
    )


def test_allowlist_covers_all_pipeline_events():
    """Spec: every pipeline status milestone fans out."""
    for ev in (
        "lead.stage_changed",
        "lead.qualified",
        "lead.won",
        "lead.lost",
        "lead.disqualified",
    ):
        assert ev in OUTBOUND_EVENT_ALLOWLIST


def test_fanout_minimal_mode_body_is_event_type_plus_external_id():
    """`payload_mode=minimal` → only event_type + external_id."""
    adapter, redis = _make_adapter_with_config(payload_mode="minimal")
    asyncio.run(adapter.publish(_event("lead.qualified", external_id="A-999")))

    redis.rpush.assert_called_once()
    _, payload = redis.rpush.call_args.args
    job = json.loads(payload)
    body = json.loads(job["body"])
    assert body == {"event_type": "lead.qualified", "external_id": "A-999"}


def test_fanout_full_mode_body_includes_lead_snapshot():
    """`payload_mode=full` adds tenant/lead ids, stages, and the lead doc."""
    adapter, redis = _make_adapter_with_config(payload_mode="full")
    evt = _event("lead.stage_changed", external_id="A-999")
    evt.payload = {
        "from_stage": "contacted",
        "to_stage": "converted",
        "changed_by": "user-1",
        "lead": {"id": "lead-1", "name": "Test", "status": "converted"},
    }
    asyncio.run(adapter.publish(evt))

    _, payload = redis.rpush.call_args.args
    body = json.loads(json.loads(payload)["body"])
    assert body["event_type"] == "lead.stage_changed"
    assert body["external_id"] == "A-999"
    assert body["from_stage"] == "contacted"
    assert body["to_stage"] == "converted"
    assert body["changed_by"] == "user-1"
    assert body["lead"]["status"] == "converted"
    assert body["origin_system"] == "partner_intranet"


def test_fanout_event_id_is_deterministic_per_timestamp():
    """event_id = '<external_id>.<event_type>.<ts_ms>' so concurrent retries
    dedupe identically while different transitions stay distinct."""
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.qualified", external_id="A-999")))

    _, payload = redis.rpush.call_args.args
    job = json.loads(payload)
    # 2026-04-24 12:00:00 UTC → 1777377600000
    assert job["event_id"] == "A-999.lead.qualified.1777377600000"


def test_fanout_falls_back_to_lead_id_when_no_external_id():
    """Native CRM lead (no partner upstream) still fires — receiver gets
    qualifier lead_id as the external_id."""
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.qualified", external_id=None)))
    redis.rpush.assert_called_once()
    _, payload = redis.rpush.call_args.args
    body = json.loads(json.loads(payload)["body"])
    assert body["external_id"] == "22222222-2222-2222-2222-222222222222"


def test_fanout_falls_back_when_external_id_blank():
    """Whitespace-only external_id falls back to lead_id, not dropped."""
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.qualified", external_id="   ")))
    redis.rpush.assert_called_once()


def test_fanout_emits_won_and_lost():
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.won", external_id="A-1")))
    asyncio.run(adapter.publish(_event("lead.lost", external_id="A-2")))
    assert redis.rpush.call_count == 2


def test_fanout_emits_stage_changed():
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.stage_changed", external_id="A-1")))
    redis.rpush.assert_called_once()


def test_fanout_drops_score_updated():
    """score.updated is internal-only — must never leave the qualifier."""
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("score.updated")))
    redis.rpush.assert_not_called()


def test_fanout_drops_pre_score_judged():
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("pre_score.judged")))
    redis.rpush.assert_not_called()


def test_fanout_drops_lead_created_and_updated():
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.created")))
    asyncio.run(adapter.publish(_event("lead.updated")))
    redis.rpush.assert_not_called()


def test_fanout_respects_tenant_opt_out():
    """Tenant can disable a pipeline event via enabled_events filtering."""
    adapter, redis = _make_adapter_with_config(enabled_events=["score.*"])
    asyncio.run(adapter.publish(_event("lead.qualified", external_id="A-999")))
    redis.rpush.assert_not_called()
