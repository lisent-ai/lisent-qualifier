"""Loop-prevention tests: outbound headers + suppression for partner-origin leads.

Covers the two contract pieces partner integrators rely on:

1. `deliver()` extracts `origin_system` / `external_id` from the body and
   emits them as `X-Lisent-Origin` / `X-Lisent-External-Id` headers so
   receivers can upsert without parsing the body.
2. `WebhookFanoutAdapter.publish()` suppresses lifecycle (`lead.*`) events
   for leads with `origin_system == "partner_intranet"` — these would
   echo the partner's own data back to them. Scoring events still flow.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from app.adapters.event.webhook_fanout import WebhookFanoutAdapter
from app.application.webhook.delivery import _extract_origin_headers, deliver
from app.ports.event import ScoreEvent


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
    # Pre-existing headers still present
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


# ──────────────────────────────────────────────── fanout loop suppression


def _make_adapter_with_config():
    """Adapter wired with an in-memory tenant config cache so publish() skips DB."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps({
        "url": "https://partner.example.com/hook",
        "secret": "s",
        "enabled_events": ["*"],
        "payload_mode": "full",
    }))
    redis.rpush = AsyncMock()
    redis.setex = AsyncMock()
    pool = MagicMock()
    return WebhookFanoutAdapter(redis=redis, db_pool=pool), redis


def _event(event_type: str, origin: str | None):
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
        external_id="A-999" if origin else None,
        origin_system=origin,
        source="intranet" if origin else None,
    )


def test_fanout_suppresses_lead_events_for_partner_origin():
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.created", "partner_intranet")))
    asyncio.run(adapter.publish(_event("lead.updated", "partner_intranet")))
    redis.rpush.assert_not_called()


def test_fanout_allows_score_events_for_partner_origin():
    """Score updates MUST still flow to partner — that's the integration's point.
    The body carries external_id so the partner can upsert by foreign ID.
    """
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("score.updated", "partner_intranet")))
    redis.rpush.assert_called_once()
    _, payload = redis.rpush.call_args.args
    job = json.loads(payload)
    body = json.loads(job["body"])
    assert body["external_id"] == "A-999"
    assert body["origin_system"] == "partner_intranet"
    assert body["source"] == "intranet"


def test_fanout_allows_lead_events_for_native_origin():
    """Lifecycle events from natively-created leads are NOT suppressed."""
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("lead.created", None)))
    redis.rpush.assert_called_once()


def test_fanout_strips_empty_origin_fields_from_body():
    adapter, redis = _make_adapter_with_config()
    asyncio.run(adapter.publish(_event("score.updated", None)))
    _, payload = redis.rpush.call_args.args
    body = json.loads(json.loads(payload)["body"])
    assert "external_id" not in body
    assert "origin_system" not in body
    assert "source" not in body
