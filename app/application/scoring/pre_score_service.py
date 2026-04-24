"""PreScoreService — OSINT + ensemble + DB writeback + event publish.

Worker bu servisin tek public method'una (`score`) delege eder. Responsibilities:

1. Lead'i DB'den oku
2. OSINT enrichment (DBCached → SelfHosted veya Stub)
3. PreScoreEnsemble (3 persona, median + formula audit + divergence)
4. Ensemble tamamen fail ederse → data_quality_fallback
5. qualifier_leads UPDATE: score + score_breakdown JSONB + extra_data JSONB
6. EventPort.publish(ScoreEvent(event_type="pre_score.judged", ...))

Idempotent: aynı lead_id birden fazla kez score edilirse son skor yazılır
(retry mantığıyla uyumlu; at-least-once semantics).

Graceful degradation:
    - OSINT fail → ensemble yine çalışır (sadece form verisiyle)
    - Ensemble fail → fallback score (≤ 45 clamped)
    - DB write fail → exception propagate (worker retry scheduler'a düşer)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from app.application.scoring.pre_score_ensemble import (
    EnsembleResult,
    run_pre_score_ensemble,
)
from app.domain.scoring.data_quality_fallback import (
    FallbackScore,
    compute_fallback_score,
)
from app.ports.event import ScoreEvent
from app.ports.osint import OSINTPort, OSINTProfile

if TYPE_CHECKING:
    import asyncpg

    from app.ports.event import EventPort

log = structlog.get_logger(__name__)


# ============================================================================
# Result container
# ============================================================================

@dataclass(frozen=True)
class PreScoreServiceOutcome:
    """Servis çıktısı — worker metrics/logging için."""

    lead_id: UUID
    tenant_id: UUID
    final_score: int
    used_fallback: bool
    divergent: bool = False
    divergence_abs: int = 0
    osint_cache_hit: bool = False
    osint_provider: str = ""
    ensemble_result: EnsembleResult | None = None
    fallback_result: FallbackScore | None = None
    elapsed_ms: int = 0
    notes: list[str] = field(default_factory=list)


# ============================================================================
# Service
# ============================================================================

class PreScoreService:
    """OSINT + ensemble + DB writeback + event publish orchestration.

    Tenant context: RLS aktif olduğu için DB call'lar `SET LOCAL app.tenant_id`
    içeren bir connection ile yapılmalı. Worker her iş öncesi bir connection
    acquire edip ayarlar.

    Args:
        osint:       OSINTPort (ör. DBCachedOSINTAdapter(SelfHostedOSINTAdapter))
        event_port:  EventPort (Redis PubSub + webhook fanout composite)
        divergence_threshold: LLM vs formula |delta| bu eşiği aşarsa flag
    """

    def __init__(
        self,
        *,
        osint: OSINTPort,
        event_port: EventPort,
        divergence_threshold: int = 20,
    ) -> None:
        self._osint = osint
        self._event_port = event_port
        self._divergence_threshold = divergence_threshold

    async def score(
        self,
        *,
        tenant_id: UUID,
        lead_id: UUID,
        conn: asyncpg.Connection,
        ideal_customer_profile: str = "",
        sector: str = "construction",
        qualification_threshold: int = 75,
    ) -> PreScoreServiceOutcome:
        started = time.monotonic()

        lead_row = await self._fetch_lead(conn, lead_id)
        if lead_row is None:
            raise ValueError(f"Lead not found: tenant={tenant_id} lead_id={lead_id}")

        form = self._extract_form(lead_row)
        osint_profile = await self._run_osint(tenant_id, form)

        # Try ensemble; on total failure, fall back to data-quality scoring
        ensemble: EnsembleResult | None = None
        fallback: FallbackScore | None = None
        used_fallback = False

        try:
            ensemble = await run_pre_score_ensemble(
                lead_json=form,
                osint_json=osint_profile.to_dict(),
                ideal_customer_profile=ideal_customer_profile,
                sector=sector,
                divergence_threshold=self._divergence_threshold,
            )
            final_score = ensemble.median_direct_score
        except Exception as exc:
            log.warning(
                "pre_score_ensemble_total_failure_falling_back",
                tenant_id=str(tenant_id),
                lead_id=str(lead_id),
                error=f"{type(exc).__name__}: {exc}",
            )
            fallback = compute_fallback_score(form, reason="ensemble_all_failed")
            final_score = fallback.total
            used_fallback = True

        await self._writeback(
            conn,
            lead_id=lead_id,
            tenant_id=tenant_id,
            final_score=final_score,
            osint_profile=osint_profile,
            ensemble=ensemble,
            fallback=fallback,
        )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        path = "fast" if final_score >= qualification_threshold else "chat"

        await self._publish_event(
            tenant_id=tenant_id,
            lead_id=lead_id,
            final_score=final_score,
            threshold=qualification_threshold,
            path=path,
            osint_profile=osint_profile,
            ensemble=ensemble,
            fallback=fallback,
        )

        outcome = PreScoreServiceOutcome(
            lead_id=lead_id,
            tenant_id=tenant_id,
            final_score=final_score,
            used_fallback=used_fallback,
            divergent=ensemble.divergent if ensemble else False,
            divergence_abs=ensemble.divergence_abs if ensemble else 0,
            osint_cache_hit=osint_profile.cache_hit,
            osint_provider=osint_profile.provider,
            ensemble_result=ensemble,
            fallback_result=fallback,
            elapsed_ms=elapsed_ms,
            notes=list(osint_profile.notes),
        )
        log.info(
            "pre_score_completed",
            tenant_id=str(tenant_id),
            lead_id=str(lead_id),
            final_score=final_score,
            used_fallback=used_fallback,
            divergent=outcome.divergent,
            osint_cache_hit=osint_profile.cache_hit,
            osint_provider=osint_profile.provider,
            elapsed_ms=elapsed_ms,
        )
        return outcome

    # ─── Internals ─────────────────────────────────────────────────────────

    async def _fetch_lead(
        self,
        conn: asyncpg.Connection,
        lead_id: UUID,
    ) -> dict[str, Any] | None:
        """qualifier_leads row'u oku. RLS aktif → tenant kontrolü DB tarafında."""
        row = await conn.fetchrow(
            """
            SELECT id, tenant_id, phone, name, email, city, source,
                   project_type, budget_range, extra_data, raw_payload
            FROM qualifier_leads
            WHERE id = $1::uuid
            """,
            str(lead_id),
        )
        if row is None:
            return None
        d = dict(row)
        d["id"] = str(d["id"])
        d["tenant_id"] = str(d["tenant_id"]) if d.get("tenant_id") else None
        for field_name in ("extra_data", "raw_payload"):
            if isinstance(d.get(field_name), str):
                d[field_name] = json.loads(d[field_name])
            elif d.get(field_name) is None:
                d[field_name] = {}
        return d

    def _extract_form(self, lead_row: dict[str, Any]) -> dict[str, Any]:
        """DB row'dan LLM'in göreceği flat form dict'i oluştur."""
        notes = ""
        extra = lead_row.get("extra_data") or {}
        if isinstance(extra, dict):
            notes = extra.get("notes") or extra.get("note") or ""
            if not notes:
                # raw_payload fallback
                raw = lead_row.get("raw_payload") or {}
                if isinstance(raw, dict):
                    notes = raw.get("notes") or raw.get("message") or raw.get("note") or ""
        return {
            "name": lead_row.get("name") or "",
            "email": lead_row.get("email") or "",
            "phone": lead_row.get("phone") or "",
            "city": lead_row.get("city") or "",
            "source": lead_row.get("source") or "",
            "project_type": lead_row.get("project_type") or "",
            "budget_range": lead_row.get("budget_range") or "",
            "notes": notes,
        }

    async def _run_osint(
        self,
        tenant_id: UUID,
        form: dict[str, Any],
    ) -> OSINTProfile:
        """OSINT enrichment. Exception yerine stub profile döner — graceful."""
        from datetime import UTC, datetime
        try:
            return await self._osint.enrich(
                email=form.get("email") or None,
                phone=form.get("phone") or None,
                name=form.get("name") or None,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            log.warning(
                "pre_score_osint_failed",
                tenant_id=str(tenant_id),
                error=f"{type(exc).__name__}: {exc}",
            )
            return OSINTProfile(
                provider="stub_osint_failure",
                fetched_at=datetime.now(UTC),
                notes=[f"osint fetch failed: {type(exc).__name__}"],
            )

    async def _writeback(
        self,
        conn: asyncpg.Connection,
        *,
        lead_id: UUID,
        tenant_id: UUID,
        final_score: int,
        osint_profile: OSINTProfile,
        ensemble: EnsembleResult | None,
        fallback: FallbackScore | None,
    ) -> None:
        """UPDATE qualifier_leads SET score, score_breakdown, extra_data.

        score_breakdown JSONB:
            { "pre_score_ensemble": {...}, "pre_score_fallback": {...}? }
        extra_data JSONB:
            { ..., "osint": {...} } — mevcut key'ler korunur
        """
        breakdown: dict[str, Any] = {}
        if ensemble is not None:
            breakdown["pre_score_ensemble"] = ensemble.to_jsonb_breakdown()
        if fallback is not None:
            breakdown["pre_score_fallback"] = {
                "total": fallback.total,
                "email_points": fallback.email_points,
                "phone_points": fallback.phone_points,
                "city_points": fallback.city_points,
                "notes_points": fallback.notes_points,
                "reason": fallback.reason,
            }
        # fit_score = final_score (CompositeScorer downstream için uyumluluk)
        breakdown["fit_score"] = final_score

        osint_dict = osint_profile.to_dict()

        # extra_data JSONB merge: mevcut key'ler korunur, osint eklenir
        await conn.execute(
            """
            UPDATE qualifier_leads
               SET score           = $1,
                   score_breakdown = $2::jsonb,
                   extra_data      = COALESCE(extra_data, '{}'::jsonb)
                                     || jsonb_build_object('osint', $3::jsonb),
                   updated_at      = now()
             WHERE id = $4::uuid
            """,
            final_score,
            json.dumps(breakdown),
            json.dumps(osint_dict),
            str(lead_id),
        )

    async def _publish_event(
        self,
        *,
        tenant_id: UUID,
        lead_id: UUID,
        final_score: int,
        threshold: int,
        path: str,
        osint_profile: OSINTProfile,
        ensemble: EnsembleResult | None,
        fallback: FallbackScore | None,
    ) -> None:
        """ScoreEvent(event_type='pre_score.judged') yayınla. Best-effort."""
        payload: dict[str, Any] = {
            "osint": {
                "provider": osint_profile.provider,
                "cache_hit": osint_profile.cache_hit,
                "phone_country": osint_profile.phone.country,
                "email_domain_type": osint_profile.email.domain_type,
                "email_site_count": osint_profile.email.site_count,
            },
        }
        if ensemble is not None:
            payload["ensemble"] = {
                "median_direct_score": ensemble.median_direct_score,
                "formula_audit_score": ensemble.formula_audit_score,
                "divergent": ensemble.divergent,
                "persona_scores": ensemble.persona_scores,
                "extraction_confidence": round(
                    ensemble.extraction_confidence, 3,
                ),
                "sales_context": ensemble.aggregated_signals.sales_context.model_dump(),
            }
        if fallback is not None:
            payload["fallback"] = {
                "reason": fallback.reason,
                "total": fallback.total,
            }

        event = ScoreEvent(
            tenant_id=tenant_id,
            lead_id=lead_id,
            session_id=None,
            event_type="pre_score.judged",
            score=final_score,
            threshold=threshold,
            path=path,
            payload=payload,
        )
        try:
            await self._event_port.publish(event)
        except Exception as exc:
            # Event publish fail etmek skorlama işini bozmasın (event gene
            # SSE replay üzerinden de yakalanabilir, webhook retry mevcut)
            log.warning(
                "pre_score_event_publish_failed",
                tenant_id=str(tenant_id),
                lead_id=str(lead_id),
                error=f"{type(exc).__name__}: {exc}",
            )
