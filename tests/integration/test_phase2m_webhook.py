"""
Phase 2.M — Outbound webhooks integration tests.

Covered:
    - TestSigning        — sign_payload roundtrip, tamper detect, ts window
    - TestCompositeFanout — publish reaches all adapters; one failure doesn't
                            block the other
    - TestWebhookFanout   — enqueues only when tenant has url+secret
    - TestDelivery        — mock consumer receives POST with correct HMAC +
                            all Lisent headers; 2xx=success, 5xx=fail
    - TestWorker          — retry is scheduled on failure and promoted back
                            to the queue; max attempts → DLQ
    - TestWebhookConfig   — GET/PATCH/POST /v1/config/webhook* endpoints

Requires the dev stack (Postgres 5433 + Redis 6380).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import pytest

try:
    import asyncpg
    import httpx
    import redis.asyncio as redis_async
    from httpx import ASGITransport, AsyncClient

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _HAS_DEPS, reason="asyncpg, httpx or redis missing"),
]


APP_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://app:qualifierpass@host.docker.internal:5433/lead_qualifier",
)
REDIS_DSN = os.getenv("REDIS_DSN", "redis://host.docker.internal:6380/0")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def admin_conn():
    try:
        conn = await asyncpg.connect(APP_DB_URL)
    except Exception as exc:
        pytest.skip(f"DB unreachable: {exc}")
    try:
        yield conn
    finally:
        await conn.close()


async def _reset_tenant(admin_conn, slug: str) -> None:
    # asyncpg uses prepared statements when $N params are present, which
    # forbids multi-statement SQL. Three separate executes instead.
    await admin_conn.execute(
        """
        DELETE FROM qualifier_sessions WHERE lead_id IN (
            SELECT id FROM qualifier_leads WHERE tenant_id IN (
                SELECT id FROM tenants WHERE slug=$1
            )
        )
        """,
        slug,
    )
    await admin_conn.execute(
        "DELETE FROM qualifier_leads WHERE tenant_id IN (SELECT id FROM tenants WHERE slug=$1)",
        slug,
    )
    await admin_conn.execute("DELETE FROM tenants WHERE slug=$1", slug)


@pytest.fixture
async def tenant_hook(admin_conn):
    slug = "webhook-test"
    await _reset_tenant(admin_conn, slug)
    await admin_conn.execute(
        """
        INSERT INTO tenants (slug, name, source_type, plan, status, config,
                             outbound_webhook_url, outbound_webhook_secret)
        VALUES ($1, 'Webhook Test', 'standalone', 'pro', 'active', '{}'::jsonb,
                'http://mock-consumer.test/hook', 'test-secret-0123456789')
        """,
        slug,
    )
    row = await admin_conn.fetchrow(
        "SELECT id, slug, outbound_webhook_url, outbound_webhook_secret FROM tenants WHERE slug=$1",
        slug,
    )
    yield dict(row)
    await _reset_tenant(admin_conn, slug)


@pytest.fixture
async def tenant_no_hook(admin_conn):
    slug = "webhook-test-off"
    await _reset_tenant(admin_conn, slug)
    await admin_conn.execute(
        """
        INSERT INTO tenants (slug, name, source_type, plan, status, config)
        VALUES ($1, 'Webhook Off', 'standalone', 'pro', 'active', '{}'::jsonb)
        """,
        slug,
    )
    row = await admin_conn.fetchrow("SELECT id, slug FROM tenants WHERE slug=$1", slug)
    yield dict(row)
    await _reset_tenant(admin_conn, slug)


@pytest.fixture
async def redis_client():
    try:
        r = redis_async.from_url(REDIS_DSN, decode_responses=False)
        await r.ping()
    except Exception as exc:
        pytest.skip(f"Redis unreachable: {exc}")
    # Clean webhook keys before each test
    await r.delete("webhook:queue", "webhook:retry")
    cursor = 0
    for pattern in ("webhook:dlq:*", "webhook:inflight:*", "webhook:tenant_cfg:*"):
        while True:
            cursor, keys = await r.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                await r.delete(*keys)
            if cursor == 0:
                break
    try:
        yield r
    finally:
        await r.aclose()


@pytest.fixture
async def api_client():
    from app.main import app as _app

    async with _app.router.lifespan_context(_app):
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://test", timeout=30.0) as client:
            yield client


def _auth(slug: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {slug}"}


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSigning:
    def test_roundtrip(self):
        from app.application.webhook.signing import sign_payload, verify_signature

        secret = "top-secret"
        body = b'{"score":42}'
        ts = int(time.time() * 1000)
        sig = sign_payload(secret, ts, body)
        assert sig.startswith("sha256=")
        assert verify_signature(secret, ts, body, sig, now_ms=ts) is True

    def test_tampered_body_rejected(self):
        from app.application.webhook.signing import sign_payload, verify_signature

        secret = "top-secret"
        body = b'{"score":42}'
        ts = int(time.time() * 1000)
        sig = sign_payload(secret, ts, body)
        assert verify_signature(secret, ts, b'{"score":999}', sig, now_ms=ts) is False

    def test_expired_timestamp_rejected(self):
        from app.application.webhook.signing import sign_payload, verify_signature

        secret = "top-secret"
        body = b'{"score":42}'
        ts = int(time.time() * 1000)
        sig = sign_payload(secret, ts, body)
        # Consumer clock 10 minutes ahead → outside 300s window.
        assert verify_signature(secret, ts, body, sig, now_ms=ts + 600_000) is False

    def test_wrong_secret_rejected(self):
        from app.application.webhook.signing import sign_payload, verify_signature

        body = b'{"score":42}'
        ts = int(time.time() * 1000)
        sig = sign_payload("sender-secret", ts, body)
        assert verify_signature("attacker-guess", ts, body, sig, now_ms=ts) is False


class TestCompositeFanout:
    async def test_publish_reaches_all_adapters(self):
        from uuid import uuid4

        from app.adapters.event.composite import CompositeEventAdapter
        from app.ports.event import EventPort, ScoreEvent

        calls: list[str] = []

        class _Fake(EventPort):
            def __init__(self, name: str, fail: bool = False) -> None:
                self.name = name
                self.fail = fail

            async def publish(self, event):  # type: ignore[override]
                calls.append(self.name)
                if self.fail:
                    raise RuntimeError(f"{self.name} intentionally failed")

            async def subscribe(self, tenant_id, session_id=None):  # type: ignore[override]
                if False:
                    yield  # pragma: no cover

        composite = CompositeEventAdapter(_Fake("a", fail=True), _Fake("b"))
        await composite.publish(
            ScoreEvent(
                tenant_id=uuid4(),
                lead_id=uuid4(),
                session_id=None,
                event_type="score.updated",
                score=1,
                threshold=75,
                path="chat",
            )
        )
        # Even though `a` raises, `b` still runs — failures isolated.
        assert calls == ["a", "b"]


class TestWebhookFanoutAdapter:
    async def test_enqueues_when_configured(
        self, tenant_hook, redis_client, admin_conn
    ):
        from app.adapters.event.webhook_fanout import WebhookFanoutAdapter
        from app.infrastructure.db.pool import get_db_pool, init_db_pool
        from app.ports.event import ScoreEvent

        await init_db_pool(APP_DB_URL)
        adapter = WebhookFanoutAdapter(redis_client, get_db_pool())

        await adapter.publish(
            ScoreEvent(
                tenant_id=tenant_hook["id"],
                lead_id=uuid.uuid4(),
                session_id=None,
                event_type="lead.qualified",
                score=88,
                threshold=75,
                path="crm",
                external_id="partner-lead-001",
            )
        )

        depth = await redis_client.llen("webhook:queue")
        assert depth == 1
        raw = await redis_client.lpop("webhook:queue")
        job = json.loads(raw)
        assert job["tenant_id"] == str(tenant_hook["id"])
        assert job["event_type"] == "lead.qualified"
        assert job["attempt"] == 0
        assert job["event_id"] == "partner-lead-001.lead.qualified"
        assert json.loads(job["body"]) == {
            "event_type": "lead.qualified",
            "external_ref": "partner-lead-001",
        }

    async def test_skips_when_tenant_has_no_webhook(
        self, tenant_no_hook, redis_client
    ):
        from app.adapters.event.webhook_fanout import WebhookFanoutAdapter
        from app.infrastructure.db.pool import get_db_pool, init_db_pool
        from app.ports.event import ScoreEvent

        await init_db_pool(APP_DB_URL)
        adapter = WebhookFanoutAdapter(redis_client, get_db_pool())

        await adapter.publish(
            ScoreEvent(
                tenant_id=tenant_no_hook["id"],
                lead_id=uuid.uuid4(),
                session_id=None,
                event_type="lead.qualified",
                score=50,
                threshold=75,
                path="crm",
                external_id="partner-lead-002",
            )
        )

        depth = await redis_client.llen("webhook:queue")
        assert depth == 0


class TestDelivery:
    async def test_hmac_and_headers_on_success(self):
        from app.application.webhook.delivery import deliver
        from app.application.webhook.signing import verify_signature

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = request.content
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
            ts = int(time.time() * 1000)
            outcome = await deliver(
                url="http://consumer.test/hook",
                secret="s3cr3t",
                body=b'{"hello":"world"}',
                event_id="111222333",
                event_type="score.updated",
                delivery_id="d-1",
                tenant_id=str(uuid.uuid4()),
                timestamp_ms=ts,
                client=client,
            )
        assert outcome.ok is True
        assert outcome.status_code == 200
        assert captured["method"] == "POST"
        h = captured["headers"]
        assert h["x-lisent-event-id"] == "111222333"
        assert h["x-lisent-event-type"] == "score.updated"
        assert h["x-lisent-delivery-id"] == "d-1"
        assert h["x-lisent-timestamp"] == str(ts)
        assert h["x-lisent-signature"].startswith("sha256=")
        assert h["content-type"] == "application/json"
        assert verify_signature(
            "s3cr3t", ts, b'{"hello":"world"}', h["x-lisent-signature"], now_ms=ts
        )

    async def test_non_2xx_is_failure(self):
        from app.application.webhook.delivery import deliver

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0) as client:
            outcome = await deliver(
                url="http://consumer.test/hook",
                secret="s",
                body=b"{}",
                event_id="1",
                event_type="score.updated",
                delivery_id="d",
                tenant_id="t",
                client=client,
            )
        assert outcome.ok is False
        assert outcome.status_code == 500
        assert outcome.error == "http_500"


class TestWorkerRetryAndDLQ:
    async def test_retry_scheduled_on_failure_then_dlq(
        self, tenant_hook, redis_client, admin_conn, monkeypatch
    ):
        """Force every delivery to fail → observe attempts counter climb to
        MAX_ATTEMPTS then land in DLQ."""
        from app.application.webhook import config as wcfg
        from app.application.webhook.worker import WebhookWorker
        from app.infrastructure.db.pool import get_db_pool, init_db_pool

        # Tight retry schedule so the test stays fast (total ~2s).
        monkeypatch.setattr(wcfg, "RETRY_DELAYS_SECONDS", (0, 0, 0, 0))
        monkeypatch.setattr(wcfg, "MAX_ATTEMPTS", 4)
        from app.application.webhook import worker as worker_mod

        monkeypatch.setattr(worker_mod, "MAX_ATTEMPTS", 4)
        monkeypatch.setattr(worker_mod, "RETRY_DELAYS_SECONDS", (0, 0, 0, 0))

        # Always-500 transport
        call_counter = {"n": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            call_counter["n"] += 1
            return httpx.Response(503)

        await init_db_pool(APP_DB_URL)
        worker = WebhookWorker(redis_client, get_db_pool())
        await worker.start()
        # Swap the worker's httpx client for a mock-transport one so we never
        # actually hit the network.
        await worker._client.aclose()
        worker._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=5.0
        )
        try:
            # Manually enqueue a job for the test tenant.
            job = {
                "tenant_id": str(tenant_hook["id"]),
                "event_id": "e-1",
                "event_type": "score.updated",
                "body": json.dumps({"test": True}),
                "attempt": 0,
                "delivery_id": "d-1",
                "first_enqueued_at_ms": int(time.time() * 1000),
            }
            await redis_client.rpush("webhook:queue", json.dumps(job))

            # Wait for all attempts + DLQ
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                dlq_size = await redis_client.llen(
                    f"webhook:dlq:{tenant_hook['id']}"
                )
                if dlq_size >= 1:
                    break
                await asyncio.sleep(0.1)
            assert dlq_size == 1, f"expected 1 DLQ entry, got {dlq_size} after {call_counter['n']} calls"
            assert call_counter["n"] == 4
            raw = await redis_client.lindex(f"webhook:dlq:{tenant_hook['id']}", 0)
            entry = json.loads(raw)
            assert entry["tenant_id"] == str(tenant_hook["id"])
            assert entry["event_id"] == "e-1"
            assert entry["attempt"] == 3  # 0..3, exhausted on the 4th
            assert entry["dlq_reason"] == "http_503"
        finally:
            await worker.stop()


class TestWebhookConfigEndpoints:
    async def test_patch_sets_url_and_auto_generates_secret(
        self, api_client, admin_conn
    ):
        slug = "webhook-endpoint-test"
        await _reset_tenant(admin_conn, slug)
        await admin_conn.execute(
            """
            INSERT INTO tenants (slug, name, source_type, plan, status, config)
            VALUES ($1, 'Webhook Endpoint Test', 'standalone', 'pro', 'active', '{}'::jsonb)
            """,
            slug,
        )
        try:
            resp = await api_client.patch(
                "/v1/config/webhook",
                headers=_auth(slug),
                json={"url": "https://consumer.example.com/hook"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["url"] == "https://consumer.example.com/hook"
            assert body["has_secret"] is True
            # Secret returned on first set (none existed before).
            assert isinstance(body["secret"], str) and len(body["secret"]) >= 30

            # Subsequent GET must NOT include the secret.
            g = await api_client.get("/v1/config/webhook", headers=_auth(slug))
            assert g.status_code == 200
            data = g.json()
            assert data["url"] == "https://consumer.example.com/hook"
            assert data["has_secret"] is True
            assert "secret" not in data
            assert data["dlq_size"] == 0
        finally:
            await _reset_tenant(admin_conn, slug)

    async def test_rotate_secret(self, api_client, admin_conn):
        slug = "webhook-rotate-test"
        await _reset_tenant(admin_conn, slug)
        await admin_conn.execute(
            """
            INSERT INTO tenants (slug, name, source_type, plan, status, config,
                                 outbound_webhook_url, outbound_webhook_secret)
            VALUES ($1, 'Rotate Test', 'standalone', 'pro', 'active', '{}'::jsonb,
                    'https://consumer.example.com/hook', 'original-secret')
            """,
            slug,
        )
        try:
            resp = await api_client.patch(
                "/v1/config/webhook",
                headers=_auth(slug),
                json={"rotate_secret": True},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            new_secret = body["secret"]
            assert new_secret and new_secret != "original-secret"

            # Stored value matches returned plaintext.
            row = await admin_conn.fetchrow(
                "SELECT outbound_webhook_secret FROM tenants WHERE slug=$1", slug
            )
            assert row["outbound_webhook_secret"] == new_secret
        finally:
            await _reset_tenant(admin_conn, slug)

    async def test_post_test_endpoint_enqueues(
        self, api_client, admin_conn, redis_client
    ):
        """Worker from api_client lifespan may consume the job before we peek,
        so accept the job being in any of: queue / retry / DLQ. The tenant
        URL (fake `consumer.example.com`) will fail delivery, so one of the
        failure-side buckets should fill."""
        slug = "webhook-testpost"
        await _reset_tenant(admin_conn, slug)
        await admin_conn.execute(
            """
            INSERT INTO tenants (slug, name, source_type, plan, status, config,
                                 outbound_webhook_url, outbound_webhook_secret)
            VALUES ($1, 'Test Post', 'standalone', 'pro', 'active', '{}'::jsonb,
                    'https://consumer.example.com/hook', 'secret-abc')
            """,
            slug,
        )
        try:
            resp = await api_client.post(
                "/v1/config/webhook/test",
                headers=_auth(slug),
                json={"score": 77},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["enqueued"] is True

            row = await admin_conn.fetchrow(
                "SELECT id FROM tenants WHERE slug=$1", slug
            )
            tenant_id = str(row["id"])

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                q = await redis_client.llen("webhook:queue")
                r = await redis_client.zcard("webhook:retry")
                d = await redis_client.llen(f"webhook:dlq:{tenant_id}")
                if q + r + d >= 1:
                    assert True
                    return
                await asyncio.sleep(0.1)
            pytest.fail("event did not land in queue/retry/dlq within 5s")
        finally:
            await _reset_tenant(admin_conn, slug)

    async def test_post_test_rejects_when_unconfigured(
        self, api_client, admin_conn
    ):
        slug = "webhook-test-noconf"
        await _reset_tenant(admin_conn, slug)
        await admin_conn.execute(
            """
            INSERT INTO tenants (slug, name, source_type, plan, status, config)
            VALUES ($1, 'No Conf', 'standalone', 'pro', 'active', '{}'::jsonb)
            """,
            slug,
        )
        try:
            resp = await api_client.post(
                "/v1/config/webhook/test",
                headers=_auth(slug),
                json={"score": 50},
            )
            assert resp.status_code == 409, resp.text
        finally:
            await _reset_tenant(admin_conn, slug)
