"""Phase 3 — DBCachedOSINTAdapter unit tests.

Pattern: monkeypatch `app.infrastructure.db.osint_profile_repo` functions to
control fresh/stale/miss branches without spinning up Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.adapters.osint.db_cached import DBCachedOSINTAdapter
from app.ports.osint import OSINTError, OSINTPort, OSINTProfile

pytestmark = pytest.mark.asyncio


class _RecordingWrapped(OSINTPort):
    """Test double — counts calls, returns canned profile."""

    def __init__(self, profile: OSINTProfile | None = None, raise_exc: Exception | None = None):
        self.calls: list[dict[str, Any]] = []
        self._profile = profile
        self._raise = raise_exc

    @property
    def name(self) -> str:
        return "fake_inner"

    async def enrich(
        self,
        *,
        email: str | None,
        phone: str | None,
        name: str | None = None,
        tenant_id: UUID | None = None,
    ) -> OSINTProfile:
        self.calls.append({"email": email, "phone": phone, "tenant_id": tenant_id})
        if self._raise is not None:
            raise self._raise
        assert self._profile is not None, "Test setup error: profile or raise_exc must be set"
        return self._profile


def _make_profile(provider: str = "fake_inner") -> OSINTProfile:
    return OSINTProfile(
        provider=provider,
        fetched_at=datetime.now(UTC),
        notes=["test"],
    )


class TestDBCachedOSINTAdapter:
    async def test_tenant_id_required(self):
        wrapped = _RecordingWrapped(profile=_make_profile())
        adapter = DBCachedOSINTAdapter(wrapped=wrapped, pool=object(), stale_days=60)
        with pytest.raises(OSINTError, match="tenant_id"):
            await adapter.enrich(email="a@b.com", phone="+1", tenant_id=None)
        assert wrapped.calls == []

    async def test_cache_hit_skips_wrapped_adapter(self, monkeypatch):
        cached_profile = _make_profile(provider="self_hosted")
        wrapped = _RecordingWrapped(profile=_make_profile())

        async def fake_get_fresh(pool, *, tenant_id, key_hash, stale_days):
            return cached_profile

        async def fake_upsert(*args, **kwargs):
            raise AssertionError("upsert should not be called on cache hit")

        monkeypatch.setattr(
            "app.adapters.osint.db_cached.repo.get_fresh", fake_get_fresh,
        )
        monkeypatch.setattr(
            "app.adapters.osint.db_cached.repo.upsert", fake_upsert,
        )

        adapter = DBCachedOSINTAdapter(wrapped=wrapped, pool=object(), stale_days=60)
        result = await adapter.enrich(
            email="a@b.com", phone="+905551234567", tenant_id=uuid4(),
        )

        assert wrapped.calls == []
        assert result.cache_hit is True
        assert result.provider == "self_hosted"

    async def test_cache_miss_triggers_wrapped_and_upserts(self, monkeypatch):
        upserts: list[dict[str, Any]] = []
        fresh_profile = _make_profile(provider="self_hosted")
        wrapped = _RecordingWrapped(profile=fresh_profile)

        async def fake_get_fresh(pool, *, tenant_id, key_hash, stale_days):
            return None  # miss

        async def fake_upsert(pool, *, tenant_id, key_hash, email, phone, profile):
            upserts.append({
                "tenant_id": tenant_id, "key_hash": key_hash,
                "email": email, "phone": phone, "provider": profile.provider,
            })

        monkeypatch.setattr(
            "app.adapters.osint.db_cached.repo.get_fresh", fake_get_fresh,
        )
        monkeypatch.setattr(
            "app.adapters.osint.db_cached.repo.upsert", fake_upsert,
        )

        tid = uuid4()
        adapter = DBCachedOSINTAdapter(wrapped=wrapped, pool=object(), stale_days=60)
        result = await adapter.enrich(
            email="fresh@example.com", phone="+905551234567", tenant_id=tid,
        )

        assert len(wrapped.calls) == 1
        assert wrapped.calls[0]["email"] == "fresh@example.com"
        assert len(upserts) == 1
        assert upserts[0]["tenant_id"] == tid
        assert upserts[0]["email"] == "fresh@example.com"
        assert upserts[0]["provider"] == "self_hosted"
        assert result.cache_hit is False

    async def test_stale_returns_none_from_repo_triggers_refresh(self, monkeypatch):
        """get_fresh 60-day staleness check içeride — None dönünce miss path'e düşer."""
        upserts: list[dict[str, Any]] = []
        fresh_profile = _make_profile()
        wrapped = _RecordingWrapped(profile=fresh_profile)

        async def fake_get_fresh(pool, *, tenant_id, key_hash, stale_days):
            # stale → None döner (60d threshold repo tarafında check ediliyor)
            return None

        async def fake_upsert(pool, **kwargs):
            upserts.append(kwargs)

        monkeypatch.setattr(
            "app.adapters.osint.db_cached.repo.get_fresh", fake_get_fresh,
        )
        monkeypatch.setattr(
            "app.adapters.osint.db_cached.repo.upsert", fake_upsert,
        )

        adapter = DBCachedOSINTAdapter(wrapped=wrapped, pool=object(), stale_days=60)
        await adapter.enrich(email="stale@x.com", phone=None, tenant_id=uuid4())

        assert len(wrapped.calls) == 1
        assert len(upserts) == 1

    async def test_upsert_failure_still_returns_fresh_profile(self, monkeypatch):
        """DB write fail ederse caller fresh veriyi kaybetmez."""
        fresh = _make_profile()
        wrapped = _RecordingWrapped(profile=fresh)

        async def fake_get_fresh(pool, **kwargs):
            return None

        async def fake_upsert(pool, **kwargs):
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(
            "app.adapters.osint.db_cached.repo.get_fresh", fake_get_fresh,
        )
        monkeypatch.setattr(
            "app.adapters.osint.db_cached.repo.upsert", fake_upsert,
        )

        adapter = DBCachedOSINTAdapter(wrapped=wrapped, pool=object(), stale_days=60)
        result = await adapter.enrich(email="x@y.com", phone=None, tenant_id=uuid4())
        assert result is fresh or result.cache_hit is False
        assert len(wrapped.calls) == 1

    async def test_wrapped_error_propagates_no_cache_write(self, monkeypatch):
        """Inner adapter exception → cache YAZILMAZ, exception yukarı gider."""
        upserts: list[Any] = []
        wrapped = _RecordingWrapped(raise_exc=OSINTError("upstream dead"))

        async def fake_get_fresh(pool, **kwargs):
            return None

        async def fake_upsert(pool, **kwargs):
            upserts.append(kwargs)

        monkeypatch.setattr(
            "app.adapters.osint.db_cached.repo.get_fresh", fake_get_fresh,
        )
        monkeypatch.setattr(
            "app.adapters.osint.db_cached.repo.upsert", fake_upsert,
        )

        adapter = DBCachedOSINTAdapter(wrapped=wrapped, pool=object(), stale_days=60)
        with pytest.raises(OSINTError, match="upstream dead"):
            await adapter.enrich(email="x@y.com", phone=None, tenant_id=uuid4())
        assert upserts == []
