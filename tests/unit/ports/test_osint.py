"""Phase 3 — OSINTProfile dataclass contract tests."""

from __future__ import annotations

from datetime import UTC, datetime

from app.ports.osint import (
    OSINTEmailSignals,
    OSINTPhoneSignals,
    OSINTProfile,
)


class TestOSINTProfile:
    def test_defaults_are_empty_but_well_typed(self):
        p = OSINTProfile(
            provider="stub",
            fetched_at=datetime.now(UTC),
        )
        assert p.phone.e164 is None
        assert p.email.registered_sites == []
        assert p.notes == []
        assert p.cache_hit is False
        assert p.elapsed_ms == 0

    def test_with_cache_hit_returns_new_frozen_copy(self):
        original = OSINTProfile(
            provider="self_hosted",
            fetched_at=datetime.now(UTC),
        )
        hit = original.with_cache_hit(True)
        assert original.cache_hit is False
        assert hit.cache_hit is True
        assert hit is not original

    def test_to_dict_from_dict_roundtrip_preserves_fields(self):
        fetched = datetime(2026, 4, 23, 22, 30, 0, tzinfo=UTC)
        original = OSINTProfile(
            provider="self_hosted",
            fetched_at=fetched,
            phone=OSINTPhoneSignals(
                e164="+905551234567",
                country="TR",
                country_code=90,
                local_format="0555 123 45 67",
                carrier=None,
                line_type=None,
                valid=True,
            ),
            email=OSINTEmailSignals(
                domain="onurinsaat.com.tr",
                domain_type="corporate",
                registered_sites=["linkedin", "github"],
                site_count=2,
                rate_limited_count=60,
                error_count=14,
                modules_checked=121,
                any_rate_limited=True,
            ),
            notes=["phone country=TR", "email domain_type=corporate"],
            cache_hit=False,
            elapsed_ms=8260,
        )

        d = original.to_dict()
        restored = OSINTProfile.from_dict(d)

        assert restored.provider == original.provider
        assert restored.fetched_at == original.fetched_at
        assert restored.phone == original.phone
        assert restored.email == original.email
        assert restored.notes == original.notes
        assert restored.cache_hit == original.cache_hit
        assert restored.elapsed_ms == original.elapsed_ms

    def test_from_dict_tolerates_missing_nested_keys(self):
        """DB'den okuma sırasında nested dict'ler eksik olabilir — defensive."""
        partial = {
            "provider": "stub",
            "fetched_at": "2026-04-23T22:30:00+00:00",
            # phone, email, notes yok
        }
        p = OSINTProfile.from_dict(partial)
        assert p.provider == "stub"
        assert p.phone.e164 is None
        assert p.email.registered_sites == []
        assert p.notes == []
