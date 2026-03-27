from datetime import datetime

from redis.asyncio import Redis


class ScoreRepository:
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    def _key(self, session_id: str) -> str:
        return f"scores:{session_id}"

    async def record(self, session_id: str, score: int) -> None:
        ts = datetime.utcnow().timestamp()
        await self._r.zadd(self._key(session_id), {str(score): ts})

    async def history(self, session_id: str) -> list[tuple[str, float]]:
        return await self._r.zrange(self._key(session_id), 0, -1, withscores=True)
