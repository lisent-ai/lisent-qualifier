"""Outbound webhook fanout — allowlist + minimal-body contract.

Outbound surface is restricted to status milestones (currently just
`lead.qualified`). Body is fixed: `{event_type, external_ref}`. Every
other event the qualifier emits (`score.*`, `pre_score.*`, lifecycle,
`lead.stage_changed`) is dropped at the fanout adapter and never leaves
the service.

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


def _make_adapter_with_config(enabled_events=None):
    """Adapter wired with an in-memory tenant config cache so publish() skips DB."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps({
        "url": "https://partner.example.com/hook",
        "secret": "s",
        "enabled_events": enabled_events or ["*"],
        "payload_mode": "full",
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


def test_allowlist_contains_lead_qualified():
    """Spec: lead.qualified is the one event currently fanning out."""
    assert "lead.qualified" in OUTBOUND_EVENT_ALLOWLIST


def test_fanout_emits_lead_qualified_with_minimal_body():
    """Body must contain ONLY event_type + external_ref — no score, payload,
    osint, ensemble, or anything else. Partner contract."""
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.qualified", external_id="A-999")))

    redis.rpush.assert_called_once()
    _, payload = redis.rpush.call_args.args
    job = json.loads(payload)
    body = json.loads(job["body"])
    assert body == {"event_type": "lead.qualified", "external_ref": "A-999"}


def test_fanout_event_id_is_deterministic():
    """event_id = '<external_ref>.<event_type>' so retries dedupe identically."""
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.qualified", external_id="A-999")))

    _, payload = redis.rpush.call_args.args
    job = json.loads(payload)
    assert job["event_id"] == "A-999.lead.qualified"


def test_fanout_drops_lead_qualified_without_external_ref():
    """Native CRM lead (no partner upstream) — no one to notify."""
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.qualified", external_id=None)))
    redis.rpush.assert_not_called()


def test_fanout_drops_lead_qualified_with_blank_external_ref():
    """Whitespace-only external_id is treated as missing."""
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.qualified", external_id="   ")))
    redis.rpush.assert_not_called()


def test_fanout_drops_score_updated():
    """score.updated is internal-only now — must never leave the qualifier."""
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


def test_fanout_drops_lead_stage_changed():
    """lead.stage_changed is the upstream signal that PRODUCES lead.qualified;
    we don't echo the raw transition out — just the milestone."""
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.stage_changed")))
    redis.rpush.assert_not_called()


def test_fanout_respects_tenant_opt_out():
    """Tenant can disable lead.qualified by setting enabled_events to a
    list that doesn't match."""
    adapter, redis = _make_adapter_with_config(enabled_events=["score.*"])
    asyncio.run(adapter.publish(_event("lead.qualified", external_id="A-999")))
    redis.rpush.assert_not_called()
