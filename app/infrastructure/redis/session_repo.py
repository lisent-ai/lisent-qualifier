import json
from typing import Any

from redis.asyncio import Redis

from app.domain.conversation.session import ConversationSession, SessionStage, ChatMessage


_SESSION_PREFIX = "session:"
_LOCK_PREFIX = "lock:bant:"
_OUTBOX_KEY = "crm:outbox"
_DEDUP_PREFIX = "dedup:"


class SessionRepository:
    def __init__(self, redis: Redis, ttl: int = 86400) -> None:
        self._r = redis
        self._ttl = ttl

    def _key(self, session_id: str) -> str:
        return f"{_SESSION_PREFIX}{session_id}"

    async def save(self, session: ConversationSession) -> None:
        key = self._key(session.session_id)
        data: dict[str, str] = {
            "session_id": session.session_id,
            "lead_json": json.dumps(session.lead_json, ensure_ascii=False),
            "score": str(session.score),
            "stage": str(session.stage),
            "msg_count": str(session.msg_count),
            "messages": json.dumps(
                [{"role": m.role, "content": m.content, "ts": m.ts} for m in session.messages],
                ensure_ascii=False,
            ),
            "created_at": str(session.created_at),
        }
        if session.bant_json is not None:
            data["bant_json"] = json.dumps(session.bant_json, ensure_ascii=False)
        if session.reasoning_json is not None:
            data["reasoning_json"] = json.dumps(session.reasoning_json, ensure_ascii=False)
        if session.last_bant_at is not None:
            data["last_bant_at"] = str(session.last_bant_at)
        if session.fallback_url is not None:
            data["fallback_url"] = session.fallback_url

        pipe = self._r.pipeline()
        pipe.hset(key, mapping=data)
        pipe.expire(key, self._ttl)
        await pipe.execute()

    async def get(self, session_id: str) -> ConversationSession | None:
        key = self._key(session_id)
        data = await self._r.hgetall(key)
        if not data:
            return None

        messages_raw: list[dict] = json.loads(data.get("messages", "[]"))
        messages = [ChatMessage(role=m["role"], content=m["content"], ts=m.get("ts", 0.0))
                    for m in messages_raw]

        return ConversationSession(
            session_id=data["session_id"],
            lead_json=json.loads(data["lead_json"]),
            score=int(data["score"]),
            stage=SessionStage(data["stage"]),
            msg_count=int(data.get("msg_count", 0)),
            messages=messages,
            bant_json=json.loads(data["bant_json"]) if "bant_json" in data else None,
            reasoning_json=json.loads(data["reasoning_json"]) if "reasoning_json" in data else None,
            last_bant_at=float(data["last_bant_at"]) if "last_bant_at" in data else None,
            created_at=float(data.get("created_at", 0.0)),
            fallback_url=data.get("fallback_url") or None,
        )

    async def update_score(self, session_id: str, score: int) -> None:
        key = self._key(session_id)
        await self._r.hset(key, "score", str(score))
        await self._r.expire(key, self._ttl)

    async def update_bant(self, session_id: str, bant_json: dict[str, Any], score: int) -> None:
        key = self._key(session_id)
        pipe = self._r.pipeline()
        pipe.hset(key, mapping={
            "bant_json": json.dumps(bant_json, ensure_ascii=False),
            "score": str(score),
            "last_bant_at": str(__import__("datetime").datetime.utcnow().timestamp()),
        })
        pipe.expire(key, self._ttl)
        await pipe.execute()

    async def set_stage(self, session_id: str, stage: SessionStage) -> None:
        key = self._key(session_id)
        await self._r.hset(key, "stage", str(stage))

    async def increment_msg_count(self, session_id: str) -> int:
        key = self._key(session_id)
        count = await self._r.hincrby(key, "msg_count", 1)
        await self._r.expire(key, self._ttl)
        return count

    async def delete(self, session_id: str) -> None:
        await self._r.delete(self._key(session_id))

    # ── BANT dedup lock ──────────────────────────────────────────────────────

    async def acquire_bant_lock(self, session_id: str, ttl: int = 120) -> bool:
        key = f"{_LOCK_PREFIX}{session_id}"
        result = await self._r.set(key, 1, ex=ttl, nx=True)
        return result is not None

    async def release_bant_lock(self, session_id: str) -> None:
        await self._r.delete(f"{_LOCK_PREFIX}{session_id}")

    # ── CRM outbox ───────────────────────────────────────────────────────────

    async def push_crm_outbox(self, payload: dict[str, Any]) -> None:
        await self._r.rpush(_OUTBOX_KEY, json.dumps(payload, ensure_ascii=False))

    async def pop_crm_outbox(self) -> dict[str, Any] | None:
        item = await self._r.lpop(_OUTBOX_KEY)
        if item is None:
            return None
        return json.loads(item)

    # ── Idempotency dedup ────────────────────────────────────────────────────

    async def is_duplicate_lead(self, lead_id: str, ttl: int = 3600) -> bool:
        key = f"{_DEDUP_PREFIX}{lead_id}"
        result = await self._r.set(key, 1, ex=ttl, nx=True)
        return result is None  # None means key already existed → duplicate
