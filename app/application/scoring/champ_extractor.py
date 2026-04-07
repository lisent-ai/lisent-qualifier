"""
Lead qualification extraction BackgroundTask.
Acquires Redis lock → calls LLM (CHAMP or Judge) → validates → updates composite score.
If score crosses dynamic threshold, triggers handoff.

Supports three scoring modes (per company config):
  - "champ": Traditional CHAMP extraction via local LLM (default)
  - "llm_judge": Holistic qualification via Groq-based LLM judge
  - "hybrid": Both run concurrently, highest qualification score wins
"""
import asyncio
import json
import time
import structlog

from app.config import get_settings
from app.domain.scoring.champ import CHAMPScore
from app.domain.scoring.qualification_judgment import QualificationJudgment
from app.domain.scoring.composite_scorer import CompositeScorer, CompositeResult, ScoringWeights
from app.domain.scoring.thresholds import compute_threshold
from app.domain.scoring.engagement import compute_engagement_score
from app.domain.scoring.events import ScoreEvent
from app.domain.scoring.signals.registry import SignalRegistry
from app.infrastructure.redis.session_repo import SessionRepository
from app.infrastructure.redis.score_repo import ScoreRepository
from app.infrastructure.llm import local_llm_client
from app.metrics import CHAMP_EXTRACTIONS, HANDOFF_COUNTER, SCORING_MODE_COMPARISON

log = structlog.get_logger(__name__)


async def extract_champ_task(
    session_id: str,
    session_repo: SessionRepository,
    score_repo: ScoreRepository,
    force_handoff: bool = False,
) -> None:
    """
    Idempotent extraction task.
    Safe to run concurrently — Redis lock prevents double execution.
    """
    # ── Acquire dedup lock ───────────────────────────────────────────────────
    acquired = await session_repo.acquire_champ_lock(session_id)
    if not acquired:
        log.debug("champ_lock_already_held", session_id=session_id)
        return

    try:
        session = await session_repo.get(session_id)
        if session is None:
            log.warning("champ_session_not_found", session_id=session_id)
            return

        if session.stage.value in ("HANDOFF", "HUMAN_TAKEOVER"):
            log.debug("champ_skip_stage", session_id=session_id, stage=session.stage.value)
            return

        # ── Fetch company config for language/sector/scoring_mode ────────
        company_config = await _fetch_company_config(session.company_id)
        language = (company_config or {}).get("primary_language", "tr")
        sector = (company_config or {}).get("industry_focus", "construction")
        scoring_mode = (company_config or {}).get("scoring_mode", "llm_judge")

        # ── Run extraction based on scoring mode ─────────────────────────
        if scoring_mode == "llm_judge":
            champ_json, merged = await _extract_judge_path(
                session, company_config, language, sector,
            )
        elif scoring_mode == "hybrid":
            champ_json, merged = await _extract_hybrid_path(
                session, company_config, language, sector,
            )
        else:
            # Default: traditional CHAMP extraction
            champ_json, merged = await _extract_champ_path(
                session, company_config, language, sector,
            )

        if champ_json is None or merged is None:
            return

        # ── Compute composite score ──────────────────────────────────────
        settings = get_settings()
        msg_dicts = [
            {"role": m.role, "content": m.content, "ts": m.ts}
            for m in session.messages
        ]
        eng_result = compute_engagement_score(
            msg_dicts,
            current_time=time.time(),
            decay_start_minutes=settings.engagement_decay_start_minutes,
        )

        # Negative signals: use judge output or rule-based (asymmetric 0.5x applied in CompositeScorer)
        if scoring_mode in ("llm_judge", "hybrid") and champ_json.get("negative_penalty") is not None:
            neg_adjustment = champ_json.get("negative_penalty", 0)
        else:
            neg_detector = SignalRegistry.get_negative_detector(language, sector)
            hours_inactive = _hours_since_last_message(session.messages)
            neg_result = neg_detector.detect(msg_dicts, hours_inactive)
            neg_adjustment = neg_result.total_penalty

        # Sector qualifier bonus
        sector_qualifier = SignalRegistry.get_sector_qualifier(sector)
        sector_bonus = sector_qualifier.compute_bonus(champ_json)

        # Seasonal modifier
        seasonal = SignalRegistry.get_seasonal_modifier(sector)

        # Composite score
        weights = ScoringWeights.from_config(company_config)
        scorer = CompositeScorer(weights)

        if scoring_mode in ("llm_judge", "hybrid") and champ_json.get("holistic_score"):
            # Judge mode: holistic_score is the LLM's overall assessment.
            # Use it directly instead of weighted average — the judge already
            # considered fit, qualification, engagement, and sector signals.
            # Only apply engagement as a minor boost and negatives/seasonal.
            holistic = champ_json["holistic_score"]
            eng_boost = int(eng_result.score * 0.10)  # slight engagement bonus
            # Asymmetric: negatives count half to protect real buyers
            raw_score = holistic + eng_boost + int(neg_adjustment * 0.5) + seasonal
            final_score = max(0, min(100, raw_score))

            result = CompositeResult(
                final_score=final_score,
                fit_score=session.score,
                qualification_score=merged.total if merged else 0,
                engagement_score=eng_result.score,
                negative_adjustment=neg_adjustment,
                sector_bonus=sector_bonus,
                seasonal_modifier=seasonal,
                confidence_multiplier=1.0,
                raw_weighted=float(holistic),
            )
        else:
            result = scorer.compute(
                fit_score=session.score,  # original rule-based score
                champ=merged,
                engagement_score=eng_result.score,
                negative_adjustment=neg_adjustment,
                sector_bonus=sector_bonus,
                seasonal_modifier=seasonal,
            )

        # Controlled merge: allow gentle decrease with floor protection
        settings = get_settings()
        lead_json = session.lead_json or {}
        initial_fit = lead_json.get("initial_fit_score", session.score)
        score_floor = max(
            int(initial_fit * settings.score_floor_multiplier),
            settings.score_min_floor,
        )
        new_score = CompositeScorer.controlled_merge(
            session.score,
            result,
            score_floor=score_floor,
            max_decrease=settings.score_max_decrease_per_extraction,
        )

        # ── Update session ───────────────────────────────────────────────
        await session_repo.update_champ(session_id, champ_json, new_score)
        await score_repo.record(session_id, new_score)

        log.info(
            "extraction_completed",
            session_id=session_id,
            scoring_mode=scoring_mode,
            champ_total=merged.total,
            engagement=eng_result.score,
            negative=neg_adjustment,
            old_score=session.score,
            new_score=new_score,
        )

        # ── Publish score update via Redis PubSub ────────────────────────
        from app.infrastructure.redis.client import get_redis
        redis = get_redis()
        pre_score = (session.lead_json or {}).get("initial_fit_score", 0)
        await redis.publish(
            f"score:{session_id}",
            json.dumps({
                "session_id": session_id,
                "pre_score": pre_score,
                "qualified_score": new_score,
                "score": new_score,  # backward compat
                "champ": champ_json,
                "engagement": eng_result.to_dict(),
                "composite": result.to_dict(),
            }),
        )

        # ── Handoff priority chain ────────────────────────────────────────
        from app.application.qualification.handler import HandoffHandler

        # Priority 1: Judge says ready (LLM decision, independent of score)
        if scoring_mode in ("llm_judge", "hybrid") and champ_json.get("handoff_ready"):
            log.info(
                "judge_handoff_ready",
                session_id=session_id,
                score=new_score,
                reason=champ_json.get("handoff_reason", ""),
            )
            HANDOFF_COUNTER.labels(path="chat_judge").inc()
            handler = HandoffHandler(session_repo, score_repo)
            await handler.handle(session_id)
            return

        # Priority 2: Score threshold (existing logic)
        lead_json = session.lead_json or {}
        extra = lead_json.get("extra_data", {})
        threshold = compute_threshold(
            project_type=lead_json.get("project_type") or extra.get("project_type", ""),
            budget_range=lead_json.get("budget_range") or extra.get("budget_range", ""),
            company_config=company_config,
        )

        if new_score >= threshold:
            log.info(
                "handoff_threshold_reached",
                session_id=session_id,
                score=new_score,
                threshold=threshold,
            )
            HANDOFF_COUNTER.labels(path="chat").inc()
            handler = HandoffHandler(session_repo, score_repo)
            await handler.handle(session_id)
            return

        # Priority 3: Force handoff — max messages safety net
        if force_handoff:
            log.info(
                "force_handoff_max_messages",
                session_id=session_id,
                score=new_score,
            )
            HANDOFF_COUNTER.labels(path="chat_force").inc()
            handler = HandoffHandler(session_repo, score_repo)
            await handler.handle(session_id)

    finally:
        await session_repo.release_champ_lock(session_id)


# ── Extraction paths ────────────────────────────────────────────────────────


async def _extract_champ_path(
    session,
    company_config: dict | None,
    language: str,
    sector: str,
) -> tuple[dict | None, CHAMPScore | None]:
    """Traditional CHAMP extraction via local LLM."""
    messages_for_extraction = _build_extraction_messages(session)
    if not messages_for_extraction.strip():
        return None, None

    prompt = _build_extraction_prompt(
        messages_for_extraction, session.champ_json, language, sector,
    )

    try:
        champ_result = await local_llm_client.extract_champ(
            messages_for_extraction, prompt=prompt,
        )
        CHAMP_EXTRACTIONS.labels(success="true").inc()
    except Exception as exc:
        log.warning("champ_extraction_failed", error=str(exc))
        CHAMP_EXTRACTIONS.labels(success="false").inc()
        return None, None

    new_champ = CHAMPScore(
        challenges_score=champ_result.challenges_score,
        authority_score=champ_result.authority_score,
        money_score=champ_result.money_score,
        prioritization_score=champ_result.prioritization_score,
        challenges_notes=champ_result.challenges_notes,
        authority_notes=champ_result.authority_notes,
        money_notes=champ_result.money_notes,
        prioritization_notes=champ_result.prioritization_notes,
        challenges_confidence=champ_result.challenges_confidence,
        authority_confidence=champ_result.authority_confidence,
        money_confidence=champ_result.money_confidence,
        prioritization_confidence=champ_result.prioritization_confidence,
        extraction_version=(
            CHAMPScore.from_dict(session.champ_json).extraction_version + 1
            if session.champ_json
            else 1
        ),
    )

    if session.champ_json:
        old_champ = CHAMPScore.from_dict(session.champ_json)
        merged = old_champ.merge_monotonic(new_champ)
    else:
        merged = new_champ

    return merged.to_dict(), merged


async def _extract_judge_path(
    session,
    company_config: dict | None,
    language: str,
    sector: str,
) -> tuple[dict | None, CHAMPScore | None]:
    """Holistic qualification via Groq-based LLM judge."""
    from app.infrastructure.llm.qualification_judge_client import (
        run_qualification_judge,
        run_judge_with_self_consistency,
    )

    messages_text = _build_extraction_messages(session)
    if not messages_text.strip():
        return None, None

    settings = get_settings()

    try:
        result = await run_qualification_judge(
            conversation_text=messages_text,
            lead_json=session.lead_json or {},
            current_judgment_json=session.champ_json,
            company_config=company_config,
            language=language,
            sector=sector,
        )
    except Exception as exc:
        log.warning("judge_extraction_failed", error=str(exc))
        # Fallback to CHAMP if configured
        if settings.judge_fallback_to_champ:
            log.info("judge_fallback_to_champ")
            return await _extract_champ_path(session, company_config, language, sector)
        return None, None

    extraction_version = (
        QualificationJudgment.from_dict(session.champ_json).extraction_version + 1
        if session.champ_json
        else 1
    )
    judgment = QualificationJudgment.from_judgment_result(result, extraction_version)

    # Merge monotonic with existing
    if session.champ_json and session.champ_json.get("scoring_mode") == "llm_judge":
        old = QualificationJudgment.from_dict(session.champ_json)
        judgment = old.merge_monotonic(judgment)
    elif session.champ_json:
        # Transitioning from CHAMP to judge: build judgment from existing CHAMP data
        old = QualificationJudgment.from_dict(session.champ_json)
        judgment = old.merge_monotonic(judgment)

    # Self-consistency for borderline holistic scores
    if settings.judge_borderline_low <= judgment.holistic_score <= settings.judge_borderline_high:
        try:
            consistency_result = await run_judge_with_self_consistency(
                conversation_text=messages_text,
                lead_json=session.lead_json or {},
                current_judgment_json=judgment.to_champ_dict(),
                company_config=company_config,
                language=language,
                sector=sector,
                num_passes=settings.judge_self_consistency_passes,
            )
            sc_judgment = QualificationJudgment.from_judgment_result(
                consistency_result, judgment.extraction_version,
            )
            judgment = judgment.merge_monotonic(sc_judgment)
        except Exception as exc:
            log.warning("self_consistency_failed", error=str(exc))
            # Continue with single-pass result

    champ_dict = judgment.to_champ_dict()
    champ_score = judgment.to_champ_score()
    return champ_dict, champ_score


async def _extract_hybrid_path(
    session,
    company_config: dict | None,
    language: str,
    sector: str,
) -> tuple[dict | None, CHAMPScore | None]:
    """Run both CHAMP and Judge concurrently, take the higher qualification score."""
    champ_coro = _extract_champ_path(session, company_config, language, sector)
    judge_coro = _extract_judge_path(session, company_config, language, sector)

    (champ_json, champ_merged), (judge_json, judge_merged) = await asyncio.gather(
        champ_coro, judge_coro,
    )

    # If one path failed, use the other
    if champ_json is None and judge_json is None:
        return None, None
    if champ_json is None:
        return judge_json, judge_merged
    if judge_json is None:
        return champ_json, champ_merged

    # Log comparison for A/B analysis
    delta = (judge_merged.total if judge_merged else 0) - (champ_merged.total if champ_merged else 0)
    SCORING_MODE_COMPARISON.observe(delta)
    log.info(
        "hybrid_comparison",
        session_id=getattr(session, "session_id", ""),
        champ_total=champ_merged.total if champ_merged else 0,
        judge_total=judge_merged.total if judge_merged else 0,
        judge_holistic=judge_json.get("holistic_score", 0) if judge_json else 0,
        delta=delta,
    )

    # Take higher qualification score; prefer judge for richer data
    if judge_merged and champ_merged and judge_merged.total >= champ_merged.total:
        return judge_json, judge_merged
    return champ_json, champ_merged


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_extraction_messages(session) -> str:
    """Build conversation text for extraction. Uses full history."""
    return "\n".join(
        f"{m.role.upper()}: {m.content}" for m in session.messages
    )


def _build_extraction_prompt(
    conversation_text: str,
    current_champ_json: dict | None,
    language: str,
    sector: str,
) -> str:
    """Build the full extraction prompt with few-shot examples and current state."""
    from app.domain.conversation.templates.registry import TemplateRegistry
    from app.domain.conversation.few_shots.registry import FewShotRegistry

    templates = TemplateRegistry.get_templates(language, sector)
    few_shots = FewShotRegistry.get_examples(language, sector)

    # Current CHAMP state section
    current_section = ""
    if current_champ_json:
        current_section = (
            f"## Mevcut CHAMP Durumu\n"
            f"```json\n{json.dumps(current_champ_json, ensure_ascii=False, indent=2)}\n```\n"
            f"SADECE yeni bilgi açıklanan boyutları güncelle. "
            f"Değişmeyen boyutları mevcut skorlarında bırak.\n"
        )

    prompt = templates.extraction.format(
        conversation_history=conversation_text,
        current_champ_section=current_section,
        sector_qualifiers_instruction=templates.extraction_sector_instruction,
    )

    # Prepend few-shot examples if available
    if few_shots:
        prompt = few_shots + "\n\n" + prompt

    return prompt


def _hours_since_last_message(messages: list) -> float:
    if not messages:
        return 0.0
    last_ts = max(m.ts for m in messages)
    return (time.time() - last_ts) / 3600


async def _fetch_company_config(company_id: str) -> dict | None:
    if not company_id:
        return None
    try:
        from app.infrastructure.crm.rest_client import fetch_company_ai_config
        return await fetch_company_ai_config(company_id)
    except Exception as exc:
        log.warning("company_config_fetch_failed", error=str(exc))
        return None
