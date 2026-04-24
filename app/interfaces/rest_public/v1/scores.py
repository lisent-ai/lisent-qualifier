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

import asyncio
import json
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.redis.client import get_redis
from app.interfaces.rest_public.auth import get_current_tenant
from app.interfaces.rest_public.di import get_event_port
from app.ports.event import EventPort, ScoreEvent
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
                    l.id, l.score, l.status, l.path, l.score_breakdown,
                    l.extra_data, l.updated_at,
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

    explanation: dict[str, Any] = {
        "components": {name: comp.model_dump() for name, comp in components.items()},
        "champ": champ.model_dump(),
        "recommendation": recommendation.model_dump(),
    }

    # Phase 3 — additive: pre-score ensemble breakdown + OSINT provenance
    # (yeni pre-scoring pipeline'ın ürettiği zengin kanıt + sales context).
    # Mevcut consumer'lar bu key'leri görmezden gelir — `dict[str, Any]` typed.
    if isinstance(score_breakdown, dict):
        pre_score_ensemble = score_breakdown.get("pre_score_ensemble")
        if isinstance(pre_score_ensemble, dict):
            # Glass-box için özet — raw_personas çok büyük (audit için
            # JSONB'de kalır ama payload'a sokulmaz).
            summary = {
                "median_direct_score": pre_score_ensemble.get("median_direct_score"),
                "formula_audit_score": pre_score_ensemble.get("formula_audit_score"),
                "divergent": pre_score_ensemble.get("divergent"),
                "extraction_confidence": pre_score_ensemble.get("extraction_confidence"),
                "persona_scores": pre_score_ensemble.get("persona_scores"),
                "enum_disagreements": pre_score_ensemble.get("enum_disagreements"),
            }
            # Aggregated signals + sales_context — satış ekibi glass-box'ı
            agg = pre_score_ensemble.get("aggregated_signals")
            if isinstance(agg, dict):
                summary["signals"] = {
                    "identity": agg.get("identity"),
                    "intent": agg.get("intent"),
                    "fit": agg.get("fit"),
                    "risk": agg.get("risk"),
                }
                summary["sales_context"] = agg.get("sales_context")
            explanation["pre_score_ensemble"] = summary

        fallback = score_breakdown.get("pre_score_fallback")
        if isinstance(fallback, dict):
            explanation["pre_score_fallback"] = fallback

    # OSINT provenance — extra_data.osint JSONB'den (ayrı query maliyeti
    # istemiyoruz, _fetch_lead row'da extra_data zaten mevcut).
    extra_data = _parse_jsonb(row.get("extra_data"))
    if isinstance(extra_data, dict) and isinstance(extra_data.get("osint"), dict):
        osint = extra_data["osint"]
        # Full OSINT dict — ham data zaten 1-2KB (provider + phone + email summary)
        explanation["osint"] = osint

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


# ============================================================================
# Live score stream — GET /v1/leads/{id}/score-stream
# ============================================================================
#
# Tenant-aware, resumable Server-Sent Events stream over the shared EventPort.
# Cloudflare idles the connection at 100s, so we emit heartbeat frames every
# 15s and rely on the browser's automatic `Last-Event-ID` reconnect to
# transparently replay any events the client missed.


# Matches the Redis pubsub timeout/heartbeat cadence. 15s is well under both
# Cloudflare's 100s idle cutoff and most reverse-proxy defaults.
_SSE_HEARTBEAT_SECONDS = 15.0
# Hard upper bound on a single connection. Browsers reconnect transparently so
# this just keeps server-side resources from leaking if the client goes away
# without TCP FIN (e.g. laptop lid close).
_SSE_MAX_CONNECTION_SECONDS = 600.0


def _format_event(event: ScoreEvent) -> dict[str, str]:
    """ScoreEvent → sse-starlette dict (name, id, data)."""
    event_id_ms = int(event.timestamp.timestamp() * 1000)
    data = {
        "event_id": str(event_id_ms),
        "tenant_id": str(event.tenant_id),
        "lead_id": str(event.lead_id),
        "session_id": str(event.session_id) if event.session_id else None,
        "event_type": event.event_type,
        "score": event.score,
        "threshold": event.threshold,
        "path": event.path,
        "timestamp": event.timestamp.isoformat(),
        "payload": event.payload,
    }
    return {
        "event": event.event_type,
        "id": str(event_id_ms),
        "data": json.dumps(data, separators=(",", ":")),
    }


async def _resolve_live_session(
    conn: Any, lead_id: UUID, tenant_id: UUID
) -> UUID | None:
    """Return the newest qualifier_sessions.id for this lead, scoped to tenant.

    Returns None when the lead has no session yet (fast-path lead that never
    started a chat). Caller converts that into a 404 — clients should fall back
    to polling GET /v1/leads/{id}/score in that case.
    """
    row = await conn.fetchrow(
        """
        SELECT s.id AS session_id
        FROM qualifier_leads l
        JOIN qualifier_sessions s ON s.lead_id = l.id
        WHERE l.id = $1 AND l.tenant_id = $2
        ORDER BY s.updated_at DESC NULLS LAST, s.created_at DESC NULLS LAST
        LIMIT 1
        """,
        lead_id,
        tenant_id,
    )
    return row["session_id"] if row else None


@router.get("/{lead_id}/score-stream")
async def stream_lead_score(
    lead_id: UUID,
    request: Request,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    event_port: Annotated[EventPort, Depends(get_event_port)],
) -> EventSourceResponse:
    """Live CHAMP score stream for a lead (SSE, resumable).

    Auth: same as GET /score — platform admin token or tenant API key.
    Replay: pass `Last-Event-ID` (ms-since-epoch) to resume from a reconnect.
    Heartbeat every 15s; auto-close at 600s (browser reconnects transparently).

    404: lead not found for this tenant, or lead has no session yet.
    """
    pool = get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant.id}'")
            session_id = await _resolve_live_session(conn, lead_id, tenant.id)

    if session_id is None:
        raise HTTPException(404, "Lead not found or has no active session")

    # Last-Event-ID — SSE spec: browsers auto-send it on reconnect.
    last_event_id_raw = request.headers.get("last-event-id", "")
    try:
        since_ms = int(last_event_id_raw) if last_event_id_raw else 0
    except ValueError:
        since_ms = 0

    async def event_generator():
        redis = get_redis()
        pubsub = redis.pubsub()
        channel = f"session:{session_id}"
        started_at = asyncio.get_event_loop().time()

        # Subscribe BEFORE replay so we can't miss an event that lands between
        # the replay query and the first get_message tick.
        await pubsub.subscribe(channel)

        try:
            # ── Replay phase — stored events strictly after since_ms ────────
            replayed = await event_port.replay(session_id, since_event_id_ms=since_ms)
            for ev in replayed:
                yield _format_event(ev)

            # ── Live phase ──────────────────────────────────────────────────
            while True:
                if await request.is_disconnected():
                    break
                if asyncio.get_event_loop().time() - started_at > _SSE_MAX_CONNECTION_SECONDS:
                    yield {"event": "done", "data": json.dumps({"reason": "max_connection_time"})}
                    break

                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=_SSE_HEARTBEAT_SECONDS,
                )
                if msg and msg.get("type") == "message":
                    raw = msg.get("data")
                    if raw is None:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        doc = json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning("sse_decode_failed", raw=str(raw)[:200])
                        continue

                    event = ScoreEvent(
                        tenant_id=UUID(doc.get("tenant_id", str(tenant.id))),
                        lead_id=UUID(doc["lead_id"]),
                        session_id=UUID(doc["session_id"]) if doc.get("session_id") else None,
                        event_type=doc["event_type"],
                        score=int(doc.get("score", 0)),
                        threshold=int(doc.get("threshold", 0)),
                        path=doc.get("path", ""),
                        payload=doc.get("payload") or {},
                        timestamp=datetime.fromisoformat(doc["timestamp"])
                        if doc.get("timestamp")
                        else datetime.utcnow(),
                    )
                    # Cross-tenant defence-in-depth: channel is session-scoped,
                    # but verify tenant_id in the event body in case a stale
                    # publisher crossed wires.
                    if event.tenant_id != tenant.id:
                        log.warning(
                            "sse_tenant_mismatch",
                            expected=str(tenant.id),
                            got=str(event.tenant_id),
                        )
                        continue
                    yield _format_event(event)
                else:
                    # No message within the timeout → heartbeat.
                    yield {"event": "heartbeat", "data": "{}"}
        finally:
            try:
                await pubsub.unsubscribe(channel)
            finally:
                await pubsub.close()

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
