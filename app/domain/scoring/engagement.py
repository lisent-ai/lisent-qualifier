"""
Engagement scorer — behavioral signals from conversation metadata.

Pure Python, zero LLM calls. Computes a 0-100 engagement score
from response speed, message substance, question frequency, and depth.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EngagementResult:
    score: int  # 0-100
    response_speed_pts: int
    message_substance_pts: int
    question_frequency_pts: int
    depth_pts: int
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "response_speed_pts": self.response_speed_pts,
            "message_substance_pts": self.message_substance_pts,
            "question_frequency_pts": self.question_frequency_pts,
            "depth_pts": self.depth_pts,
            "details": self.details,
        }


def compute_engagement_score(messages: list[dict]) -> EngagementResult:
    """
    Compute engagement score from conversation messages.

    Each message dict should have: {"role": str, "content": str, "ts": float}

    Scoring dimensions (total max 100):
      - Response speed:       0-30 pts
      - Message substance:    0-25 pts
      - Question frequency:   0-20 pts
      - Conversation depth:   0-25 pts
    """
    user_msgs = [m for m in messages if m.get("role") == "user"]
    details: list[str] = []

    if not user_msgs:
        return EngagementResult(
            score=0,
            response_speed_pts=0,
            message_substance_pts=0,
            question_frequency_pts=0,
            depth_pts=0,
        )

    # ── Response speed (0-30) ─────────────────────────────────────────────
    speed_pts = 0
    if len(messages) >= 2:
        gaps = _response_gaps(messages)
        if gaps:
            avg_gap = sum(gaps) / len(gaps)
            if avg_gap < 60:  # < 1 min
                speed_pts = 30
                details.append("Çok hızlı yanıt (<1dk)")
            elif avg_gap < 300:  # < 5 min
                speed_pts = 20
                details.append("Hızlı yanıt (<5dk)")
            elif avg_gap < 900:  # < 15 min
                speed_pts = 10
                details.append("Orta hızda yanıt (<15dk)")
            else:
                speed_pts = 3

    # ── Message substance (0-25) ──────────────────────────────────────────
    substance_pts = 0
    avg_length = (
        sum(len(m.get("content", "")) for m in user_msgs) / len(user_msgs)
        if user_msgs
        else 0
    )
    if avg_length > 100:
        substance_pts = 25
        details.append("Detaylı mesajlar (ort >100 karakter)")
    elif avg_length > 50:
        substance_pts = 15
        details.append("Orta uzunlukta mesajlar")
    elif avg_length > 20:
        substance_pts = 8
    else:
        substance_pts = 2

    # ── Question frequency (0-20) ─────────────────────────────────────────
    question_count = sum(1 for m in user_msgs if "?" in m.get("content", ""))
    q_pts = min(question_count * 5, 20)
    if question_count > 0:
        details.append(f"{question_count} soru sordu")

    # ── Conversation depth (0-25) ─────────────────────────────────────────
    depth_pts = min(len(user_msgs) * 3, 25)
    if len(user_msgs) >= 5:
        details.append(f"Derin sohbet ({len(user_msgs)} mesaj)")

    total = min(100, speed_pts + substance_pts + q_pts + depth_pts)

    return EngagementResult(
        score=total,
        response_speed_pts=speed_pts,
        message_substance_pts=substance_pts,
        question_frequency_pts=q_pts,
        depth_pts=depth_pts,
        details=details,
    )


def _response_gaps(messages: list[dict]) -> list[float]:
    """
    Calculate time gaps (seconds) between assistant and user messages.
    Only measures user response time (assistant -> user gap).
    """
    gaps: list[float] = []
    prev_ts: float | None = None
    prev_role: str | None = None

    for m in messages:
        ts = m.get("ts")
        role = m.get("role")

        if ts is None or prev_ts is None:
            prev_ts = ts
            prev_role = role
            continue

        # Measure user's response time to assistant messages
        if prev_role == "assistant" and role == "user":
            gap = ts - prev_ts
            if gap > 0:
                gaps.append(gap)

        prev_ts = ts
        prev_role = role

    return gaps
