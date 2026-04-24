"""Phase 2 — data_quality_fallback panic-mode tests."""

from __future__ import annotations

from app.domain.scoring.data_quality_fallback import (
    FALLBACK_MAX,
    compute_fallback_score,
)


class TestDataQualityFallback:
    def test_empty_form_gives_zero(self):
        s = compute_fallback_score({})
        assert s.total == 0
        assert s.email_points == 0
        assert s.phone_points == 0
        assert s.city_points == 0
        assert s.notes_points == 0

    def test_all_fields_present_gives_max(self):
        s = compute_fallback_score({
            "email": "ayse@onurinsaat.com.tr",
            "phone": "+905551234567",
            "city": "Ankara",
            "notes": "Bir lead'in ilgili göründüğü somut detaylar içeren not.",
        })
        assert s.total == FALLBACK_MAX
        assert s.email_points == 15
        assert s.phone_points == 10
        assert s.city_points == 10
        assert s.notes_points == 10

    def test_disposable_email_gives_no_points(self):
        s = compute_fallback_score({
            "email": "test@mailinator.com",
            "phone": "+905551234567",
            "city": "Ankara",
            "notes": "0123456789012345678901234567",
        })
        assert s.email_points == 0
        assert s.phone_points == 10
        assert s.total <= FALLBACK_MAX

    def test_short_notes_not_counted(self):
        s = compute_fallback_score({"notes": "kısa"})
        assert s.notes_points == 0

    def test_long_notes_counted(self):
        s = compute_fallback_score({"notes": "a" * 25})
        assert s.notes_points == 10

    def test_invalid_phone_zero(self):
        s = compute_fallback_score({"phone": "123"})  # too short
        assert s.phone_points == 0

    def test_valid_phone_with_formatting(self):
        s = compute_fallback_score({"phone": "+90 (555) 123-4567"})
        assert s.phone_points == 10

    def test_total_never_exceeds_max(self):
        """Tavan zorunlu — hot lead bölgesine asla çıkmaz."""
        s = compute_fallback_score({
            "email": "a@b.com",
            "phone": "+1234567890",
            "city": "X",
            "notes": "a" * 1000,
        })
        assert s.total <= FALLBACK_MAX

    def test_reason_field_passthrough(self):
        s = compute_fallback_score({}, reason="ensemble_all_failed")
        assert s.reason == "ensemble_all_failed"

    def test_none_values_dont_crash(self):
        s = compute_fallback_score({
            "email": None, "phone": None, "city": None, "notes": None,
        })
        assert s.total == 0
