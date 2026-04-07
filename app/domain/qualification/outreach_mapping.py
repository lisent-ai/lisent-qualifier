"""
Outreach mapping — build a LeadOutreach-compatible payload from our qualification data.

Maps AI qualifier output to the CRM's LeadOutreach model columns.
Fields we can't fill are omitted (CRM will use its own defaults).

Pure domain logic — no I/O.
"""
from __future__ import annotations

import time
from typing import Any


def build_outreach_payload(
    session_id: str,
    lead_json: dict[str, Any],
    score: int,
    champ_json: dict[str, Any] | None,
    reasoning_json: dict[str, Any] | None,
    messages: list[dict[str, Any]] | None = None,
    composite_breakdown: dict[str, Any] | None = None,
    signal_summary: dict[str, Any] | None = None,
    handoff_path: str = "chat",
    ai_agent_id: str = "Lisent Engine v0 (Beta)",
) -> dict[str, Any]:
    """
    Build a payload mapped to CRM LeadOutreach columns.

    Returns a dict where keys match LeadOutreach field names.
    The CRM can use these directly or merge into its model.
    """
    now_iso = _iso_now()
    msg_list = messages or []
    user_msgs = [m for m in msg_list if m.get("role") == "user"]

    return {
        # ── Identity / routing ──────────────────────────────────────────
        "sentByType": "ai",
        "aiAgentId": ai_agent_id,
        "channel": _map_channel(lead_json.get("source", "")),
        "outreachType": f"ai_qualification_{handoff_path}",

        # ── Message content ─────────────────────────────────────────────
        "messageSubject": _build_subject(lead_json, score),
        "messageBody": _build_body(
            lead_json, score, champ_json, reasoning_json, signal_summary,
        ),
        "messagePayload": {
            "qualification_score": score,
            "champ": champ_json,
            "reasoning_report": reasoning_json,
            "composite_breakdown": composite_breakdown,
            "signal_summary": signal_summary,
            "handoff_path": handoff_path,
        },

        # ── Delivery status ─────────────────────────────────────────────
        "deliveryStatus": "delivered",
        "sentAt": now_iso,
        "deliveredAt": now_iso,

        # ── Response tracking ───────────────────────────────────────────
        "respondedAt": _first_user_ts(msg_list),
        "responseCount": len(user_msgs),
        "responseText": _last_user_message(user_msgs),
        "responsePayload": {
            "conversation_transcript": _build_transcript(msg_list),
            "signal_summary": signal_summary,
            "enriched_lead": lead_json,
        },

        # ── Conversation linking ────────────────────────────────────────
        "conversationId": session_id,
        "threadId": session_id,

        # ── Follow-up ───────────────────────────────────────────────────
        "isFollowUp": False,
        "followUpLevel": 0,
    }


def build_signal_summary(
    champ_json: dict[str, Any] | None,
    composite_breakdown: dict[str, Any] | None,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Build a human-readable signal summary for sales team.
    Includes buying signals, negative signals, key facts, and recommendation.
    """
    summary: dict[str, Any] = {
        "buying_signals": [],
        "negative_signals": [],
        "key_facts": [],
        "recommendation": "",
        "qualification_dimensions": {},
    }

    if not champ_json:
        return summary

    # ── Buying signals from judge ───────────────────────────────────────
    if champ_json.get("handoff_ready"):
        reason = champ_json.get("handoff_reason", "")
        if reason:
            summary["buying_signals"].append(reason)

    # ── Negative signals ────────────────────────────────────────────────
    neg = champ_json.get("negative_signals", [])
    if neg:
        summary["negative_signals"] = neg

    # ── Key facts from CHAMP dimensions ─────────────────────────────────
    for dim, note_key, label in [
        ("challenges", "challenges_notes", "Proje"),
        ("authority", "authority_notes", "Yetki"),
        ("money", "money_notes", "Bütçe"),
        ("prioritization", "prioritization_notes", "Zaman"),
    ]:
        note = champ_json.get(note_key) or champ_json.get(f"{dim}_reasoning", "")
        score = champ_json.get(f"{dim}_score", 0)
        if note and score > 5:
            summary["key_facts"].append(f"{label}: {note}")

    # ── Qualification dimensions ────────────────────────────────────────
    summary["qualification_dimensions"] = {
        "challenges": {
            "score": champ_json.get("challenges_score", 0),
            "max": 25,
            "confidence": champ_json.get("challenges_confidence", 0),
        },
        "authority": {
            "score": champ_json.get("authority_score", 0),
            "max": 25,
            "confidence": champ_json.get("authority_confidence", 0),
        },
        "money": {
            "score": champ_json.get("money_score", 0),
            "max": 25,
            "confidence": champ_json.get("money_confidence", 0),
        },
        "prioritization": {
            "score": champ_json.get("prioritization_score", 0),
            "max": 25,
            "confidence": champ_json.get("prioritization_confidence", 0),
        },
    }

    # ── Composite breakdown ─────────────────────────────────────────────
    if composite_breakdown:
        summary["composite_breakdown"] = composite_breakdown

    # ── Recommendation ──────────────────────────────────────────────────
    holistic = champ_json.get("holistic_score", 0)
    if holistic >= 80:
        summary["recommendation"] = "Sıcak lead — hemen arayın"
    elif holistic >= 60:
        summary["recommendation"] = "İlgili lead — detaylı teklif hazırlayın"
    elif holistic >= 40:
        summary["recommendation"] = "Ilık lead — bilgi gönderin, takip edin"
    else:
        summary["recommendation"] = "Soğuk lead — nurture kampanyasına ekleyin"

    # ── Missing info (what sales should ask) ────────────────────────────
    missing = champ_json.get("missing_info", [])
    if missing:
        summary["missing_info_for_sales"] = missing

    return summary


# ── Helpers ─────────────────────────────────────────────────────────────────


def _map_channel(source: str) -> str:
    """Map lead source to LeadOutreach channel."""
    channel_map = {
        "whatsapp": "whatsapp",
        "instagram": "internal_chat",
        "facebook": "internal_chat",
        "tiktok": "internal_chat",
        "linkedin": "internal_chat",
        "website": "internal_chat",
    }
    return channel_map.get(source, "internal_chat")


def _build_subject(lead_json: dict[str, Any], score: int) -> str:
    """Build a summary subject line for the outreach."""
    contact = lead_json.get("contact", {})
    name = contact.get("name", "Bilinmiyor")
    project = lead_json.get("project_type", "")
    return f"AI Qualified Lead: {name} — {project} — Score: {score}/100"


def _build_body(
    lead_json: dict[str, Any],
    score: int,
    champ_json: dict[str, Any] | None,
    reasoning_json: dict[str, Any] | None,
    signal_summary: dict[str, Any] | None,
) -> str:
    """Build a human-readable message body summarizing the qualification."""
    parts: list[str] = []

    contact = lead_json.get("contact", {})
    parts.append(f"Lead: {contact.get('name', 'N/A')} ({contact.get('phone', 'N/A')})")
    parts.append(f"Score: {score}/100")

    if reasoning_json:
        summary = reasoning_json.get("summary", "")
        if summary:
            parts.append(f"\n{summary}")

        approach = reasoning_json.get("recommended_approach", "")
        if approach:
            parts.append(f"\nÖnerilen yaklaşım: {approach}")

    if signal_summary:
        buying = signal_summary.get("buying_signals", [])
        if buying:
            parts.append(f"\nBuying signals: {', '.join(buying)}")

        negative = signal_summary.get("negative_signals", [])
        if negative:
            parts.append(f"\nDikkat: {', '.join(negative)}")

        rec = signal_summary.get("recommendation", "")
        if rec:
            parts.append(f"\n{rec}")

    return "\n".join(parts)


def _build_transcript(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a clean conversation transcript."""
    return [
        {
            "role": m.get("role", ""),
            "content": m.get("content", ""),
            "timestamp": m.get("ts"),
        }
        for m in messages
    ]


def _first_user_ts(messages: list[dict[str, Any]]) -> str | None:
    """ISO timestamp of the first user message."""
    for m in messages:
        if m.get("role") == "user" and m.get("ts"):
            from datetime import datetime, timezone
            return datetime.fromtimestamp(m["ts"], tz=timezone.utc).isoformat()
    return None


def _last_user_message(user_msgs: list[dict[str, Any]]) -> str:
    """Content of the last user message."""
    if user_msgs:
        return user_msgs[-1].get("content", "")
    return ""


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()
