"""
Glass Box CHAMP score endpoint — GET /v1/leads/{id}/score.

AB AI Act Ağustos 2026 zorunluluğu: yüksek-riskli AI karar sistemlerinde
(lead scoring dahil) **açıklanabilirlik + kanıt** gerekli. Bu endpoint her
skor çağrısında:
    - Overall score + threshold + status + path + framework
    - Component breakdown: fit / qualification / engagement / sector_bonus
    - CHAMP per-dimension: score + confidence + evidence + missing_info
    - Recommendation: next_action + suggested_question + priority

Rakip durumu: MadKudu fit+intent için glass box yapıyor ama CHAMP için kimse
yok. Bu Lisent'in unique differentiator'u.

Response kaynakları:
    - qualifier_leads (score, status, path, score_breakdown JSONB)
    - qualifier_sessions.champ_json (latest session — CHAMP extraction output)
    - qualifier_handoffs.reasoning_json (reasoning report, fast path için)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.infrastructure.db.pool import get_db_pool
from app.interfaces.rest_public.auth import get_current_tenant
from app.ports.tenant import Tenant

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/leads", tags=["v1-scores"])


# ============================================================================
# Glass Box schemas — "explainable" response shape
# ============================================================================


class DimensionScore(BaseModel):
    """Single CHAMP dimension — score + confidence + evidence trail."""

    score: int = Field(ge=0, le=25)
    max: int = 25
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(
        default_factory=list,
        description="Notes/citations from conversation supporting this score",
    )
    missing: list[str] = Field(
        default_factory=list,
        description="Information that would increase confidence if available",
    )


class ComponentScore(BaseModel):
    """Composite scoring component — fit / qualification / engagement / sector."""

    value: int
    weight: float
    contribution: float = Field(description="value * weight")


class ChampBreakdown(BaseModel):
    """Four CHAMP dimensions — keyed map for easy client access."""

    challenges: DimensionScore
    authority: DimensionScore
    money: DimensionScore
    prioritization: DimensionScore
    extraction_version: int = 0


class Recommendation(BaseModel):
    """AI-generated next-best-action based on CHAMP gaps."""

    next_action: str = Field(description="'schedule_call' | 'draft_email' | 'ask_question' | ...")
    suggested_question: str | None = None
    cta_type: str | None = None
    priority: str = Field(default="medium", description="'high' | 'medium' | 'low'")


class ScoreResponse(BaseModel):
    """Full Glass Box score response."""

    lead_id: UUID
    tenant_id: UUID
    framework: str = "champ"
    score: int = Field(ge=0, le=100)
    threshold: int
    status: str
    path: str | None = None
    scored_at: datetime | None = None

    explanation: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Glass Box structure: components (fit/qualification/engagement/sector), "
            "champ (4 dimensions with evidence+confidence), recommendation."
        ),
    )


# ============================================================================
# Helpers — JSONB + CHAMP parse
# ============================================================================


def _parse_jsonb(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _build_champ_breakdown(champ_json: dict[str, Any]) -> ChampBreakdown:
    """CHAMPScore.to_dict() output'undan ChampBreakdown dataclass'ı türet."""

    def _dim(prefix: str) -> DimensionScore:
        score_val = int(champ_json.get(f"{prefix}_score", 0) or 0)
        confidence = float(champ_json.get(f"{prefix}_confidence", 0.0) or 0.0)
        notes = champ_json.get(f"{prefix}_notes", "")

        # Notes → evidence list (split on newlines or sentence boundaries)
        evidence: list[str] = []
        if isinstance(notes, str) and notes.strip():
            evidence = [line.strip() for line in notes.split("\n") if line.strip()]
        elif isinstance(notes, list):
            evidence = [str(n).strip() for n in notes if str(n).strip()]

        missing = champ_json.get("missing_info", [])
        # Missing info global; filter per dimension if format supports
        per_dim_missing: list[str] = []
        if isinstance(missing, list):
            per_dim_missing = [
                m for m in missing if isinstance(m, str) and prefix.lower() in m.lower()
            ]

        return DimensionScore(
            score=score_val,
            confidence=confidence,
            evidence=evidence,
            missing=per_dim_missing,
        )

    return ChampBreakdown(
        challenges=_dim("challenges"),
        authority=_dim("authority"),
        money=_dim("money"),
        prioritization=_dim("prioritization"),
        extraction_version=int(champ_json.get("extraction_version", 0) or 0),
    )


def _build_components(score_breakdown: dict[str, Any]) -> dict[str, ComponentScore]:
    """score_breakdown JSONB → {name: ComponentScore}."""
    components: dict[str, ComponentScore] = {}
    # Expected keys from composite_scorer.py output:
    #   fit_score, qualification_score, engagement_score, sector_bonus
    # Weights plan'a göre sabit: 0.30, 0.45, 0.15, 0.10
    weights = {
        "fit_score": 0.30,
        "qualification_score": 0.45,
        "engagement_score": 0.15,
        "sector_bonus": 0.10,
    }
    for key, weight in weights.items():
        raw_value = score_breakdown.get(key)
        if raw_value is None:
            continue
        value = int(raw_value) if isinstance(raw_value, (int, float)) else 0
        components[key] = ComponentScore(
            value=value, weight=weight, contribution=round(value * weight, 2)
        )
    return components


def _build_recommendation(
    champ: ChampBreakdown,
    score: int,
    threshold: int,
    reasoning_json: dict[str, Any],
) -> Recommendation:
    """Basic rule-based recommendation (LLM-backed Phase 2.E/6'da genişler)."""
    # Reasoning report'tan al (fast path'te set edilir)
    if reasoning_json.get("recommended_approach"):
        return Recommendation(
            next_action="schedule_call" if score >= threshold else "engage_more",
            suggested_question=None,
            cta_type="high_touch" if score >= threshold else "calendly",
            priority="high" if score >= threshold else "medium",
        )

    # CHAMP gap-based: biggest gap dimension is next target
    gaps = {
        "authority": 25 - champ.authority.score,
        "challenges": 25 - champ.challenges.score,
        "money": 25 - champ.money.score,
        "prioritization": 25 - champ.prioritization.score,
    }
    biggest = max(gaps.items(), key=lambda x: x[1])

    suggested_questions = {
        "authority": "Karar vermekten sorumlu kişi siz misiniz, yoksa başka biriyle de konuşmalıyız mı?",
        "challenges": "En öncelikli ihtiyacınız / çözmeniz gereken problem nedir?",
        "money": "Ayırdığınız bütçe aralığı nedir?",
        "prioritization": "Ne zaman başlamayı / sonuçlandırmayı planlıyorsunuz?",
    }

    if score >= threshold:
        return Recommendation(
            next_action="schedule_call",
            suggested_question=None,
            cta_type="high_touch",
            priority="high",
        )
    return Recommendation(
        next_action="ask_question",
        suggested_question=suggested_questions.get(biggest[0]),
        cta_type="calendly" if score >= 50 else None,
        priority="medium" if score >= 50 else "low",
    )


# ============================================================================
# Endpoint
# ============================================================================


@router.get("/{lead_id}/score", response_model=ScoreResponse)
async def get_lead_score(
    lead_id: UUID,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> ScoreResponse:
    """Glass Box CHAMP score — full explainability response."""
    pool = get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant.id}'")

            # Lead + latest session join (CHAMP extraction'ı buradan al)
            row = await conn.fetchrow(
                """
                SELECT
                    l.id, l.score, l.status, l.path, l.score_breakdown, l.updated_at,
                    s.champ_json, s.updated_at AS session_updated_at,
                    h.reasoning_json
                FROM qualifier_leads l
                LEFT JOIN LATERAL (
                    SELECT champ_json, updated_at FROM qualifier_sessions
                    WHERE lead_id = l.id
                    ORDER BY updated_at DESC
                    LIMIT 1
                ) s ON TRUE
                LEFT JOIN LATERAL (
                    SELECT reasoning_json FROM qualifier_handoffs
                    WHERE lead_id = l.id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) h ON TRUE
                WHERE l.id = $1 AND l.tenant_id = $2
                """,
                lead_id,
                tenant.id,
            )

    if row is None:
        raise HTTPException(404, "Lead not found")

    score = int(row["score"] or 0)
    score_breakdown = _parse_jsonb(row["score_breakdown"])
    champ_json = _parse_jsonb(row["champ_json"])
    reasoning_json = _parse_jsonb(row["reasoning_json"])

    # Threshold from tenant config (Phase 2.F multi-framework'te genişler)
    threshold = int(tenant.config.get("qualification_threshold", 75))

    champ = _build_champ_breakdown(champ_json)
    components = _build_components(score_breakdown)
    recommendation = _build_recommendation(champ, score, threshold, reasoning_json)

    # Scored timestamp: fresh scoring varsa son session update_at, yoksa lead updated_at
    scored_at = row.get("session_updated_at") or row["updated_at"]

    explanation = {
        "components": {name: comp.model_dump() for name, comp in components.items()},
        "champ": champ.model_dump(),
        "recommendation": recommendation.model_dump(),
    }

    return ScoreResponse(
        lead_id=lead_id,
        tenant_id=tenant.id,
        framework=tenant.qualification_framework.value,
        score=score,
        threshold=threshold,
        status=row["status"] or "new",
        path=row.get("path"),
        scored_at=scored_at,
        explanation=explanation,
    )
