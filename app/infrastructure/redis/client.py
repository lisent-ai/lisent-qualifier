from redis.asyncio import Redis, ConnectionPool

_pool: ConnectionPool | None = None


def create_redis_pool(redis_dsn: str) -> ConnectionPool:
    global _pool
    _pool = ConnectionPool.from_url(
        redis_dsn,
        decode_responses=True,
        max_connections=20,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    return _pool


def get_redis() -> Redis:
    if _pool is None:
        raise RuntimeError("Redis pool not initialized. Call create_redis_pool() first.")
    return Redis(connection_pool=_pool)


async def close_redis_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
