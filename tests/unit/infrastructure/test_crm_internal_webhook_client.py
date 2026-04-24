"""Unit tests for the Phase 7.B CRM internal webhook client.

Focus: signing correctness, enabled/disabled gating, 4xx vs 5xx retry semantics.
Network is mocked via httpx.MockTransport — no real requests.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from app.infrastructure.crm.internal_webhook_client import (
    CRMInternalWebhookClient,
    get_crm_internal_webhook_client,
    reset_crm_internal_webhook_client,
)


def _install_transport(monkeypatch, handler):
    """Replace httpx.AsyncClient globally with one that always uses our
    MockTransport. Restored automatically when the test exits."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class _Patched(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("httpx.AsyncClient", _Patched)
    return transport


def _make_client(secret: str = "topsecret") -> CRMInternalWebhookClient:
    return CRMInternalWebhookClient(
        url="http://crm.local/internal/webhooks/qualifier/pre-score",
        secret=secret,
        timeout=2.0,
    )


@pytest.mark.asyncio
async def test_disabled_when_url_empty():
    client = CRMInternalWebhookClient(url="", secret="x", timeout=1.0)
    assert client.enabled() is False
    ok = await client.send_pre_score(
        tenant_id="t", lead_id="l", crm_lead_id="c",
        score=80, threshold=75, path="fast", payload={},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_disabled_when_secret_empty():
    client = CRMInternalWebhookClient(url="http://x/y", secret="", timeout=1.0)
    assert client.enabled() is False


@pytest.mark.asyncio
async def test_skips_when_crm_lead_id_missing(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"status": "accepted"})

    _install_transport(monkeypatch, handler)
    client = _make_client()
    ok = await client.send_pre_score(
        tenant_id="t", lead_id="l", crm_lead_id="",
        score=80, threshold=75, path="fast", payload={},
    )
    assert ok is False
    assert len(calls) == 0


@pytest.mark.asyncio
async def test_sends_signed_payload(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(200, json={"status": "accepted"})

    _install_transport(monkeypatch, handler)
    client = _make_client(secret="s3cret")
    ok = await client.send_pre_score(
        tenant_id="tid-1",
        lead_id="lid-1",
        crm_lead_id="crm-1",
        score=82,
        threshold=75,
        path="fast",
        payload={"ensemble": {"median_direct_score": 82}},
        event_id="evt-1",
    )
    assert ok is True

    envelope = json.loads(captured["body"])
    assert envelope["event_type"] == "pre_score.judged"
    assert envelope["event_id"] == "evt-1"
    assert envelope["crm_lead_id"] == "crm-1"
    assert envelope["score"] == 82
    assert envelope["path"] == "fast"
    assert envelope["payload"]["ensemble"]["median_direct_score"] == 82

    assert captured["headers"]["x-lisent-signature"].startswith("sha256=")
    assert captured["headers"]["x-lisent-event-id"] == "evt-1"
    ts_ms = captured["headers"]["x-lisent-timestamp"]
    mac = hmac.new(
        b"s3cret",
        msg=f"{ts_ms}.".encode() + captured["body"],
        digestmod=hashlib.sha256,
    )
    expected = f"sha256={mac.hexdigest()}"
    assert captured["headers"]["x-lisent-signature"] == expected


@pytest.mark.asyncio
async def test_4xx_does_not_retry_and_returns_true(monkeypatch):
    """4xx is a permanent client-side issue. Log and swallow — retry would
    waste Groq quota / CRM resources."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, json={"error": "bad signature"})

    _install_transport(monkeypatch, handler)
    client = _make_client()
    ok = await client.send_pre_score(
        tenant_id="t", lead_id="l", crm_lead_id="c",
        score=80, threshold=75, path="fast", payload={},
    )
    assert ok is True
    assert call_count == 1


@pytest.mark.asyncio
async def test_5xx_retries_three_times_then_returns_false(monkeypatch):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(503, json={"error": "oops"})

    _install_transport(monkeypatch, handler)
    client = _make_client()
    ok = await client.send_pre_score(
        tenant_id="t", lead_id="l", crm_lead_id="c",
        score=80, threshold=75, path="fast", payload={},
    )
    assert ok is False
    assert call_count == 3  # tenacity stop_after_attempt(3)


@pytest.mark.asyncio
async def test_singleton_cache(monkeypatch):
    """get_crm_internal_webhook_client returns the same instance until reset."""
    monkeypatch.setenv("CRM_INTERNAL_WEBHOOK_URL", "http://a/b")
    monkeypatch.setenv("CRM_INTERNAL_WEBHOOK_SECRET", "s")
    import app.config as config_mod
    config_mod._settings = None

    reset_crm_internal_webhook_client()
    c1 = get_crm_internal_webhook_client()
    c2 = get_crm_internal_webhook_client()
    assert c1 is c2
    reset_crm_internal_webhook_client()
    c3 = get_crm_internal_webhook_client()
    assert c3 is not c1
