"""PreScoreEnsemble — 3 paralel persona orchestrator + aggregation.

Phase 2. 3 farklı persona (skeptic, neutral, opportunity) paralel olarak
(asyncio.gather) aynı lead'i değerlendirir. Her biri structured signals +
direct_score + sales_context üretir. Ensemble:

    1. Medyan direct_score → ANA SKOR (audit yerine production signal)
    2. Majority-vote enum aggregation (2/3 uyuşma → net; aksi → "disagreement")
    3. Evidence arrays union
    4. Sales_context = highest extraction_confidence olan persona'dan
    5. Formula composer paralel shadow → divergence > 20 ise flag

Cost: 3 Groq call × ~$0.0006 ≈ $0.0018/lead. DB cache ile ortalama ~$0.001.
Latency: asyncio.gather paralel = tek çağrı süresi (~2-3s).

Closed-won learning loop altyapısı: tüm raw persona outputs ve formula_score
`score_breakdown` JSONB'sine yazılır (Phase 4 ML training data anchor'ı).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.domain.scoring.composer import (
    compose_pre_score,
    compute_median_score,
    is_divergent,
)
from app.domain.scoring.pre_score_judgment import (
    FitSignals,
    IdentitySignals,
    IntentSignals,
    PreScoreJudgmentResult,
    RiskSignals,
    SalesContext,
)
from app.infrastructure.llm.pre_score_judge_client import (
    PersonaName,
    run_pre_score_persona,
)

log = structlog.get_logger(__name__)


DEFAULT_PERSONAS: tuple[PersonaName, ...] = ("skeptic", "neutral", "opportunity")

# Majority-vote eşleşme kurallarında kullanılan sentinel: 3 persona 3 farklı
# değer verdi → "disagreement" flag'lenir. Most common (1/3) alınmaz.
_DISAGREEMENT_MARKER = "__disagreement__"


# ============================================================================
# Output dataclasses
# ============================================================================

@dataclass(frozen=True)
class PersonaFailure:
    """Bir persona çağrısı fail ettiğinde kaydedilir."""

    persona: PersonaName
    error: str


@dataclass(frozen=True)
class EnsembleResult:
    """3 persona çıktısının birleşmiş sonucu.

    `median_direct_score`: Ana skor (LLM-based). Bu `qualifier_leads.score`'a yazılır.
    `formula_audit_score`: Formül shadow — aggregation signals üzerinde çalışır.
    `divergent`: True ise |median - formula| > threshold.
    `raw_personas`: Her persona'nın raw çıktısı (glass-box + future ML training için).
    `aggregated_signals`: Majority-vote sinyal birleşimi (composer input'u).
    `sales_context`: Highest-confidence persona'nın sales_context'i.
    """

    median_direct_score: int
    formula_audit_score: int
    divergent: bool
    divergence_abs: int
    extraction_confidence: float       # mean across personas
    aggregated_signals: PreScoreJudgmentResult  # aggregate + composer input
    raw_personas: dict[str, PreScoreJudgmentResult]  # {"skeptic": result, ...}
    # {"skeptic": 32, "neutral": 38, "opportunity": 45}
    persona_scores: dict[str, int]
    failures: list[PersonaFailure] = field(default_factory=list)
    # Format: "identity.name_quality: weak/plausible/plausible"
    enum_disagreements: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    def to_jsonb_breakdown(self) -> dict[str, Any]:
        """score_breakdown.pre_score_ensemble JSONB'ye yazılacak yapı.

        Schema closed-won ML training için tasarlandı: her feature çıkartılabilir.
        """
        return {
            "median_direct_score": self.median_direct_score,
            "formula_audit_score": self.formula_audit_score,
            "divergent": self.divergent,
            "divergence_abs": self.divergence_abs,
            "extraction_confidence": round(self.extraction_confidence, 3),
            "enum_disagreements": self.enum_disagreements,
            "persona_scores": self.persona_scores,
            "failures": [{"persona": f.persona, "error": f.error} for f in self.failures],
            "elapsed_ms": self.elapsed_ms,
            # Aggregated signals (composer girdisi) — debugging için
            "aggregated_signals": self.aggregated_signals.model_dump(),
            # Raw persona outputs (future ML + audit için; büyük JSONB ama cost yok)
            "raw_personas": {
                name: r.model_dump() for name, r in self.raw_personas.items()
            },
        }


# ============================================================================
# Aggregation helpers
# ============================================================================

def _majority_vote_enum(
    values: list[str],
    disagreements: list[str],
    label: str,
) -> str:
    """2/3 veya 3/3 eşleşme varsa en sık değeri dön. Aksi takdirde ilk değeri
    dön ve `disagreements` listesine log at.

    Not: 1-1-1 durumunda ilkin döndürüyoruz (tamamen tie-breaker); bu
    genelde nadir. Ama `disagreements` listesi sales team için görünür olur.
    """
    if not values:
        return ""
    counter = Counter(values)
    most_common_value, most_common_count = counter.most_common(1)[0]
    if most_common_count >= 2:
        return most_common_value
    # All different (3 unique)
    disagreements.append(f"{label}: {'/'.join(values)}")
    return values[0]  # first persona (skeptic) as tie-breaker


def _majority_vote_bool(values: list[bool]) -> bool:
    """2/3 threshold."""
    return sum(values) >= 2


def _average_int(values: list[int]) -> int:
    return int(round(sum(values) / len(values))) if values else 0


def _union_strings(all_values: list[list[str]], max_items: int = 10) -> list[str]:
    """Birleştir, duplicate'ları kaldır, sırayı koru."""
    seen: set[str] = set()
    out: list[str] = []
    for values in all_values:
        for v in values:
            if v not in seen:
                out.append(v)
                seen.add(v)
                if len(out) >= max_items:
                    return out
    return out


def _pick_best_sales_context(
    personas: dict[str, PreScoreJudgmentResult],
) -> SalesContext:
    """Highest extraction_confidence persona'nın sales_context'i.

    Ties: skeptic < neutral < opportunity (neutral en "normal" pick).
    """
    if not personas:
        raise ValueError("No personas to pick sales_context from")
    order = ["neutral", "opportunity", "skeptic"]  # preference at equal confidence
    best_name: str | None = None
    best_conf = -1.0
    for name in order:
        if name not in personas:
            continue
        conf = personas[name].extraction_confidence
        if conf > best_conf:
            best_conf = conf
            best_name = name
    # If none of preferred names are present, fall back to max
    if best_name is None:
        best_name = max(personas, key=lambda k: personas[k].extraction_confidence)
    return personas[best_name].sales_context


def _aggregate_signals(
    personas: dict[str, PreScoreJudgmentResult],
) -> tuple[PreScoreJudgmentResult, list[str]]:
    """3 persona'nın sinyallerinden aggregated PreScoreJudgmentResult üretir.

    - Enum alanları: majority vote (2/3); tie (1-1-1) → first + disagreement log
    - Bool alanları: majority (≥2)
    - Int alanları (counts): average
    - Evidence arrays: union (max 8)
    - extraction_confidence: mean
    - sales_context: highest-confidence persona
    - direct_score: median (composer için irrelevant ama struct tamlığı için set)
    """
    disagreements: list[str] = []
    results = list(personas.values())

    identity = IdentitySignals(
        name_quality=_majority_vote_enum(
            [r.identity.name_quality for r in results], disagreements, "identity.name_quality",
        ),
        email_domain_class=_majority_vote_enum(
            [r.identity.email_domain_class for r in results],
            disagreements, "identity.email_domain_class",
        ),
        phone_validity=_majority_vote_enum(
            [r.identity.phone_validity for r in results],
            disagreements, "identity.phone_validity",
        ),
        osint_digital_footprint=_majority_vote_enum(
            [r.identity.osint_digital_footprint for r in results],
            disagreements, "identity.osint_digital_footprint",
        ),
        evidence=_union_strings([r.identity.evidence for r in results], max_items=8),
    )

    intent = IntentSignals(
        project_specificity=_majority_vote_enum(
            [r.intent.project_specificity for r in results],
            disagreements, "intent.project_specificity",
        ),
        budget_signal=_majority_vote_enum(
            [r.intent.budget_signal for r in results],
            disagreements, "intent.budget_signal",
        ),
        timeline_signal=_majority_vote_enum(
            [r.intent.timeline_signal for r in results],
            disagreements, "intent.timeline_signal",
        ),
        authority_signal=_majority_vote_enum(
            [r.intent.authority_signal for r in results],
            disagreements, "intent.authority_signal",
        ),
        buying_stage=_majority_vote_enum(
            [r.intent.buying_stage for r in results],
            disagreements, "intent.buying_stage",
        ),
        urgency_cues=_union_strings([r.intent.urgency_cues for r in results], max_items=4),
        evidence=_union_strings([r.intent.evidence for r in results], max_items=8),
    )

    # segment_label: highest-confidence persona'nın etiketi (narratif alan)
    _best_conf_name = max(personas, key=lambda k: personas[k].extraction_confidence)
    _best_segment = personas[_best_conf_name].fit.segment_label

    fit = FitSignals(
        icp_alignment=_majority_vote_enum(
            [r.fit.icp_alignment for r in results], disagreements, "fit.icp_alignment",
        ),
        project_type_in_tenant_scope=_majority_vote_enum(
            [r.fit.project_type_in_tenant_scope for r in results],
            disagreements, "fit.project_type_in_tenant_scope",
        ),
        geography_in_scope=_majority_vote_enum(
            [r.fit.geography_in_scope for r in results], disagreements, "fit.geography_in_scope",
        ),
        company_size_fit=_majority_vote_enum(
            [r.fit.company_size_fit for r in results], disagreements, "fit.company_size_fit",
        ),
        segment_label=_best_segment,
        evidence=_union_strings([r.fit.evidence for r in results], max_items=8),
    )

    risk = RiskSignals(
        disposable_email=_majority_vote_bool([r.risk.disposable_email for r in results]),
        suspicious_phone_pattern=_majority_vote_bool(
            [r.risk.suspicious_phone_pattern for r in results],
        ),
        data_inconsistency_count=_average_int(
            [r.risk.data_inconsistency_count for r in results],
        ),
        spam_indicator_count=_average_int(
            [r.risk.spam_indicator_count for r in results],
        ),
        competitor_mentioned=_majority_vote_bool(
            [r.risk.competitor_mentioned for r in results],
        ),
        evidence=_union_strings([r.risk.evidence for r in results], max_items=6),
    )

    sales_context = _pick_best_sales_context(personas)

    mean_confidence = sum(r.extraction_confidence for r in results) / len(results)
    median_direct = compute_median_score([r.direct_score for r in results])

    aggregated = PreScoreJudgmentResult(
        thinking="",  # ensemble'da thinking'i union etmiyoruz; raw_personas'da mevcut
        identity=identity,
        intent=intent,
        fit=fit,
        risk=risk,
        sales_context=sales_context,
        direct_score=median_direct,
        extraction_confidence=mean_confidence,
    )
    return aggregated, disagreements


# ============================================================================
# Main orchestrator
# ============================================================================

async def run_pre_score_ensemble(
    *,
    lead_json: dict[str, Any],
    osint_json: dict[str, Any],
    ideal_customer_profile: str = "",
    sector: str = "construction",
    personas: tuple[PersonaName, ...] = DEFAULT_PERSONAS,
    divergence_threshold: int = 20,
) -> EnsembleResult:
    """3 persona paralel Groq call + aggregation + formula audit.

    Fail-tolerant: 1 veya 2 persona fail ederse kalanlardan aggregation yapılır.
    3'ü de fail ederse exception.
    """
    import time
    started = time.monotonic()

    tasks = [
        asyncio.create_task(
            run_pre_score_persona(
                persona=p,
                lead_json=lead_json,
                osint_json=osint_json,
                ideal_customer_profile=ideal_customer_profile,
                sector=sector,
            ),
            name=f"prescore-{p}",
        )
        for p in personas
    ]
    raw = await asyncio.gather(*tasks, return_exceptions=True)

    successful: dict[str, PreScoreJudgmentResult] = {}
    failures: list[PersonaFailure] = []
    for persona_name, result in zip(personas, raw, strict=True):
        if isinstance(result, Exception):
            failures.append(
                PersonaFailure(
                    persona=persona_name,
                    error=f"{type(result).__name__}: {str(result)[:200]}",
                ),
            )
            log.warning("pre_score_persona_failed_in_ensemble",
                        persona=persona_name, error=str(result))
        else:
            successful[persona_name] = result

    if not successful:
        raise RuntimeError(
            f"All {len(personas)} pre-score personas failed: "
            f"{[f.error for f in failures]}"
        )

    aggregated, enum_disagreements = _aggregate_signals(successful)
    formula = compose_pre_score(aggregated)
    median = aggregated.direct_score  # already median
    divergent = is_divergent(median, formula.total_audit, threshold=divergence_threshold)

    elapsed_ms = int((time.monotonic() - started) * 1000)

    result = EnsembleResult(
        median_direct_score=median,
        formula_audit_score=formula.total_audit,
        divergent=divergent,
        divergence_abs=abs(median - formula.total_audit),
        extraction_confidence=aggregated.extraction_confidence,
        aggregated_signals=aggregated,
        raw_personas=successful,
        persona_scores={name: r.direct_score for name, r in successful.items()},
        failures=failures,
        enum_disagreements=enum_disagreements,
        elapsed_ms=elapsed_ms,
    )

    log.info(
        "pre_score_ensemble_completed",
        median=median,
        formula=formula.total_audit,
        divergent=divergent,
        confidence=round(aggregated.extraction_confidence, 2),
        successful_personas=len(successful),
        failed_personas=len(failures),
        enum_disagreements=len(enum_disagreements),
        elapsed_ms=elapsed_ms,
    )
    return result
