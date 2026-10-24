"""Redis: rate limiting, heartbeat presence, distributed locks, pub/sub.

The signalling gateway runs behind 4 Gunicorn workers, so a candidate socket
and the admin socket watching them will often land on *different processes*.
Redis pub/sub is what makes cross-worker routing work; without it the admin
grid silently shows nothing for three quarters of candidates.
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from app.core.config import settings

_pool: aioredis.ConnectionPool | None = None


def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.redis_url, decode_responses=True, max_connections=64
        )
    return aioredis.Redis(connection_pool=_pool)


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.disconnect()
        _pool = None


# ------------------------------------------------------------- rate limiting
async def rate_limit_hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Fixed-window counter. Returns ``(allowed, remaining)``."""
    r = get_redis()
    redis_key = f"rl:{key}:{int(time.time() // window_seconds)}"
    pipe = r.pipeline()
    pipe.incr(redis_key)
    pipe.expire(redis_key, window_seconds)
    count, _ = await pipe.execute()
    remaining = max(0, limit - int(count))
    return int(count) <= limit, remaining


async def reset_rate_limit(key: str, window_seconds: int) -> None:
    r = get_redis()
    await r.delete(f"rl:{key}:{int(time.time() // window_seconds)}")


# ------------------------------------------------------------------ presence
def _hb_key(session_id: str) -> str:
    return f"hb:session:{session_id}"


async def record_heartbeat(session_id: str, payload: dict) -> None:
    r = get_redis()
    pipe = r.pipeline()
    pipe.hset(_hb_key(session_id), mapping={k: str(v) for k, v in payload.items()})
    pipe.expire(_hb_key(session_id), settings.HEARTBEAT_TTL_SECONDS)
    pipe.sadd("hb:active", session_id)
    await pipe.execute()


async def get_heartbeat(session_id: str) -> dict | None:
    r = get_redis()
    data = await r.hgetall(_hb_key(session_id))
    return data or None


async def list_active_sessions() -> list[dict]:
    """Return live heartbeats, pruning members whose key already expired."""
    r = get_redis()
    ids = await r.smembers("hb:active")
    out: list[dict] = []
    stale: list[str] = []
    for sid in ids:
        data = await r.hgetall(_hb_key(sid))
        if data:
            data["session_id"] = sid
            out.append(data)
        else:
            stale.append(sid)
    if stale:
        await r.srem("hb:active", *stale)
    return out


async def clear_heartbeat(session_id: str) -> None:
    r = get_redis()
    pipe = r.pipeline()
    pipe.delete(_hb_key(session_id))
    pipe.srem("hb:active", session_id)
    await pipe.execute()


# --------------------------------------------------------------------- locks
@asynccontextmanager
async def distributed_lock(name: str, ttl_seconds: int = 10, wait: bool = False):
    """SET NX lock with a token-checked release, so a slow holder cannot
    delete a lock that has since been acquired by someone else."""
    r = get_redis()
    key = f"lock:{name}"
    token = uuid.uuid4().hex
    acquired = await r.set(key, token, nx=True, ex=ttl_seconds)
    if not acquired and wait:
        deadline = time.monotonic() + ttl_seconds
        while time.monotonic() < deadline and not acquired:
            import asyncio

            await asyncio.sleep(0.05)
            acquired = await r.set(key, token, nx=True, ex=ttl_seconds)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            # Compare-and-delete
            script = (
                "if redis.call('get', KEYS[1]) == ARGV[1] "
                "then return redis.call('del', KEYS[1]) else return 0 end"
            )
            try:
                await r.eval(script, 1, key, token)
            except Exception:  # pragma: no cover - lock expiry is acceptable
                pass
