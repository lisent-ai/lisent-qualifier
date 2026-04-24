"""Phase 3 — StubOSINTAdapter unit tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.adapters.osint.stub import StubOSINTAdapter

pytestmark = pytest.mark.asyncio


class TestStubOSINTAdapter:
    async def test_name_is_stub(self):
        assert StubOSINTAdapter().name == "stub"

    async def test_returns_empty_profile_with_provider_stub(self):
        adapter = StubOSINTAdapter()
        profile = await adapter.enrich(email="a@b.com", phone="+12345")

        assert profile.provider == "stub"
        assert profile.phone.e164 is None
        assert profile.email.registered_sites == []
        assert profile.elapsed_ms == 0
        assert profile.cache_hit is False
        assert "stub" in " ".join(profile.notes).lower()

    async def test_none_inputs_add_note(self):
        profile = await StubOSINTAdapter().enrich(email=None, phone=None)
        joined = " ".join(profile.notes).lower()
        assert "no email or phone" in joined

    async def test_tenant_id_ignored(self):
        profile = await StubOSINTAdapter().enrich(
            email="x@y.com", phone="+1", tenant_id=uuid4(),
        )
        assert profile.provider == "stub"
