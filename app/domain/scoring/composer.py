"""Deterministic formula composer — FORMULA AUDIT LAYER.

2nd revision (2026-04-23): Ensemble pattern'inde formül PRIMARY skor değil,
SHADOW/AUDIT katmanı. LLM ensemble medianı ana skoru verir. Composer paralel
çalışır, aggregated signals üzerinden bağımsız bir skor üretir; divergence
büyükse (> 20 puan) flag atılır.

Gerekçe:
    1. Auditability: glass-box'ta "hangi sinyal kaç puan katkı" görünür
    2. Closed-won öğrenme altyapısı: formül weights ilerde ML ile calibrate
       edilebilir (quarterly retrain, A/B test)
    3. LLM guardrail: LLM çok düşük/yüksek skor verirse shadow ile anomali
       tespit edilir
    4. Sanity check: divergence > 20 → review queue'ya

Formül DEĞİŞMEZ bir doğruyu temsil etmiyor — bu yüzden primary değil. Weights
benim tahmin. Hedef: zamanla closed-won data'sıyla weights'i relearn etmek.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from app.domain.scoring.pre_score_judgment import PreScoreJudgmentResult

# ============================================================================
# Weight tables (version 1 — hand-tuned; Phase 4'te ML ile relearn edilecek)
# ============================================================================

IDENTITY_MAX = 20
INTENT_MAX = 35
FIT_MAX = 35
RISK_MIN_PENALTY = -20

_NAME_QUALITY_POINTS = {
    "missing": 0, "random": 0, "weak": 2, "plausible": 4, "strong": 6,
}
_EMAIL_DOMAIN_POINTS = {
    # Objective buckets (debiased 2026-04-24):
    # — disposable: known fraud/throwaway signal (blocklist), penalize
    # — public_provider: gmail/outlook/yandex/… — NEUTRAL baseline.
    #   Most Turkish consumers + many SMB buyers legitimately use a
    #   gmail address; penalising it punishes real intent. Lifted to
    #   match generic_tld so email provider alone doesn't move the
    #   needle — notes/budget/project signals do.
    # — generic_tld: custom .com/.net not in public-provider set
    # — country_tld: .com.tr/.co.uk/.de etc — active in a specific
    #   market (small positive, objective)
    # — registry_tld: .gov.tr/.edu.tr/.gov/.edu — registry-tied
    #   affiliation (objective verified)
    "missing": 0,
    "disposable": -3,
    "public_provider": 3,
    "generic_tld": 3,
    "country_tld": 4,
    "registry_tld": 8,
    # Legacy enum aliases — mapped to the same neutral weights so
    # production ensemble output from before the refactor continues
    # to score on the new scale.
    "freemail": 3,              # was 2 — lifted to public_provider neutral
    "corporate_suspected": 3,   # was 5 — flattened; no subjective boost
    "corporate_verified": 8,    # stays as registry_tld
}
_PHONE_VALIDITY_POINTS = {
    "missing": 0, "invalid_format": -2, "valid_format": 2, "verified_reachable": 3,
}
_OSINT_FOOTPRINT_POINTS = {
    "none": -2, "low": 0, "medium": 2, "high": 3,
}

_PROJECT_SPECIFICITY_POINTS = {
    "none": 0, "vague": 3, "described": 8, "detailed": 12,
}
_BUDGET_SIGNAL_POINTS = {
    "absent": 0, "range_stated": 5, "specific_amount": 9,
}
_TIMELINE_SIGNAL_POINTS = {
    "absent": 0, "exploratory": 1, "short_term_soft": 4, "committed_timeline": 7,
}
_AUTHORITY_SIGNAL_POINTS = {
    "absent": 0, "influencer": 2, "joint_decider": 4, "sole_decider": 5,
}
_BUYING_STAGE_POINTS = {
    "curious_browsing": 0, "researching_options": 1,
    "actively_evaluating": 2, "ready_to_engage": 2,
}

_ICP_ALIGNMENT_POINTS = {
    "unknown": 0, "off_icp": -8, "edge_case": 0, "partial_match": 6,
    "close_match": 14, "ideal_match": 20,
}
_PROJECT_TYPE_SCOPE_POINTS = {
    "unknown": 0, "off_scope": -4, "adjacent": 2, "in_scope": 6,
}
_GEOGRAPHY_SCOPE_POINTS = {
    "unknown": 0, "outside": -3, "serviceable": 3, "core_market": 5,
}
_COMPANY_SIZE_FIT_POINTS = {
    "unknown": 0, "too_small": -2, "fit": 4, "large_enterprise": 4,
}

# Formül skoru vs LLM skoru arasındaki fark bu eşiği aşarsa "divergence" flag'i atılır
DIVERGENCE_THRESHOLD = 20


# ============================================================================
# Output dataclass
# ============================================================================

@dataclass(frozen=True)
class ComposedScore:
    """Formula audit output. Primary değil — `total_audit` alanı shadow score."""

    total_audit: int                # 0-100, formül üzerinden bağımsız skor
    identity_component: int         # 0-IDENTITY_MAX
    intent_component: int           # 0-INTENT_MAX
    fit_component: int              # 0-FIT_MAX
    risk_penalty: int               # RISK_MIN_PENALTY..0 (negatif)
    extraction_confidence: float    # input'tan kopyalanır (observability)
    regressed_toward_mean: bool     # confidence<0.5 ise raw * c + 50 * (1-c) uygulandı mı

    def to_dict(self) -> dict[str, int | float | bool]:
        return {
            "total_audit": self.total_audit,
            "identity_component": self.identity_component,
            "intent_component": self.intent_component,
            "fit_component": self.fit_component,
            "risk_penalty": self.risk_penalty,
            "extraction_confidence": self.extraction_confidence,
            "regressed_toward_mean": self.regressed_toward_mean,
        }


# ============================================================================
# Helpers
# ============================================================================

def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


# ============================================================================
# Public API
# ============================================================================

def compose_pre_score(r: PreScoreJudgmentResult) -> ComposedScore:
    """Signals → formula audit score.

    Çağıran: ensemble aggregation'dan sonra aggregated_signals üzerinde çalışır.
    Tek bir persona sonucu üzerinde de çalışır (debugging / per-persona
    component inceleme için).
    """
    # IDENTITY (0-20)
    identity = (
        _NAME_QUALITY_POINTS[r.identity.name_quality]
        + _EMAIL_DOMAIN_POINTS[r.identity.email_domain_class]
        + _PHONE_VALIDITY_POINTS[r.identity.phone_validity]
        + _OSINT_FOOTPRINT_POINTS[r.identity.osint_digital_footprint]
    )
    identity = _clamp(identity, 0, IDENTITY_MAX)

    # INTENT (0-35)
    intent = (
        _PROJECT_SPECIFICITY_POINTS[r.intent.project_specificity]
        + _BUDGET_SIGNAL_POINTS[r.intent.budget_signal]
        + _TIMELINE_SIGNAL_POINTS[r.intent.timeline_signal]
        + _AUTHORITY_SIGNAL_POINTS[r.intent.authority_signal]
        + _BUYING_STAGE_POINTS[r.intent.buying_stage]
    )
    intent = _clamp(intent, 0, INTENT_MAX)

    # FIT (0-35)
    fit = (
        _ICP_ALIGNMENT_POINTS[r.fit.icp_alignment]
        + _PROJECT_TYPE_SCOPE_POINTS[r.fit.project_type_in_tenant_scope]
        + _GEOGRAPHY_SCOPE_POINTS[r.fit.geography_in_scope]
        + _COMPANY_SIZE_FIT_POINTS[r.fit.company_size_fit]
    )
    fit = _clamp(fit, 0, FIT_MAX)

    # RISK PENALTY (-20..0)
    risk = 0
    if r.risk.disposable_email:
        risk -= 10
    if r.risk.suspicious_phone_pattern:
        risk -= 5
    risk -= min(r.risk.data_inconsistency_count * 2, 6)
    risk -= min(r.risk.spam_indicator_count * 3, 9)
    if r.risk.competitor_mentioned:
        risk -= 1
    risk = _clamp(risk, RISK_MIN_PENALTY, 0)

    raw = identity + intent + fit + risk

    # Confidence regression (düşük güven → ortalamaya (50) çek)
    conf = max(0.0, min(1.0, r.extraction_confidence))
    regressed = conf < 0.5
    if regressed:
        raw = int(round(raw * conf + 50 * (1 - conf)))

    total = _clamp(raw, 0, 100)

    return ComposedScore(
        total_audit=total,
        identity_component=identity,
        intent_component=intent,
        fit_component=fit,
        risk_penalty=risk,
        extraction_confidence=conf,
        regressed_toward_mean=regressed,
    )


def compute_median_score(direct_scores: Iterable[int]) -> int:
    """3 persona skorundan medyanı al. Primary skor burası."""
    scores = sorted(direct_scores)
    if not scores:
        return 0
    return int(median(scores))


def is_divergent(llm_score: int, formula_score: int, threshold: int = DIVERGENCE_THRESHOLD) -> bool:
    """|LLM - formula| > threshold ise True. Review queue flag için."""
    return abs(llm_score - formula_score) > threshold
