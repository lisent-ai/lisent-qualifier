"""Panic-mode fallback — LLM ensemble tamamen çöker ise son çare.

3 persona da fail ederse (Groq outage, circuit breaker open, all timeout),
LLM-free minimum viable skor üret. Skor tavanı 45 ile sınırlı — fallback
her zaman gerçek LLM judge'dan düşük bir değer dönmeli ki "fallback yaşandı"
operation'larca fark edilsin.

NOT: Bu mevcut (kaldırılan) RuleBasedScorer'dan çok daha basit. Amaç
"güvenlik ağı", yok "akıllı fallback".

Metrics: `prescore_fallback_total{reason}` artırılır.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_NONDIGIT_RE = re.compile(r"\D+")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DISPOSABLE_HINT = re.compile(
    r"@(mailinator|10minutemail|yopmail|guerrillamail|trashmail|tempmail|"
    r"sharklasers|getnada|throwaway|dispostable|tempmailo)\.",
    re.IGNORECASE,
)

FALLBACK_MAX = 45


@dataclass(frozen=True)
class FallbackScore:
    total: int                      # 0-FALLBACK_MAX
    email_points: int
    phone_points: int
    city_points: int
    notes_points: int
    reason: str                     # "ensemble_all_failed" | "osint_and_ensemble_failed" | ...


def _valid_phone_format(phone: str | None) -> bool:
    if not phone:
        return False
    digits = _NONDIGIT_RE.sub("", phone)
    return len(digits) >= 7 and len(digits) <= 15


def _plausible_email(email: str | None) -> bool:
    if not email:
        return False
    e = email.strip().lower()
    if not _EMAIL_RE.match(e):
        return False
    if _DISPOSABLE_HINT.search(e):
        return False
    return True


def compute_fallback_score(
    form: dict[str, Any],
    reason: str = "unknown",
) -> FallbackScore:
    """LLM ensemble fail ederse çağrılır. Sadece form data'ya bakar.

    Sinyaller:
        - email varsa + disposable değilse: 15 puan
        - telefon geçerli formatta: 10 puan
        - şehir boş değil: 10 puan
        - notes uzunluğu >= 20 karakter: 10 puan
    Tavan: 45 (FALLBACK_MAX). Normal ensemble skoru 40+'da hot lead;
    fallback en kötü ihtimalle "warm" bölgede kalabilir, "hot"a asla çıkmaz.
    """
    email_points = 15 if _plausible_email(form.get("email")) else 0
    phone_points = 10 if _valid_phone_format(form.get("phone")) else 0
    city_points = 10 if (form.get("city") or "").strip() else 0
    notes = form.get("notes") or ""
    notes_points = 10 if isinstance(notes, str) and len(notes) >= 20 else 0

    total = min(
        FALLBACK_MAX,
        email_points + phone_points + city_points + notes_points,
    )

    return FallbackScore(
        total=total,
        email_points=email_points,
        phone_points=phone_points,
        city_points=city_points,
        notes_points=notes_points,
        reason=reason,
    )
