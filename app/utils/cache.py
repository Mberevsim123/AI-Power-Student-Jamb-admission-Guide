"""
app/utils/cache.py
Async Redis wrapper with JSON serialization and TTL helpers.
"""

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        # Local Redis 7 does not implement RESP3 maintenance notifications.
        # RESP2 also works with older redis-py releases and our JSON/limiter commands.
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True, protocol=2)
    return _redis


class Cache:
    """
    Thin async cache helper.  All keys are namespaced under 'jamb:'.
    """

    PREFIX = "jamb:"

    @classmethod
    def _key(cls, key: str) -> str:
        return f"{cls.PREFIX}{key}"

    @classmethod
    async def get(cls, key: str) -> Optional[Any]:
        try:
            r = await get_redis()
            raw = await r.get(cls._key(key))
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.warning("Cache GET error [%s]: %s", key, exc)
            return None

    @classmethod
    async def set(cls, key: str, value: Any, ttl: int = 300) -> None:
        try:
            r = await get_redis()
            await r.setex(cls._key(key), ttl, json.dumps(value, default=str))
        except Exception as exc:
            logger.warning("Cache SET error [%s]: %s", key, exc)

    @classmethod
    async def delete(cls, key: str) -> None:
        try:
            r = await get_redis()
            await r.delete(cls._key(key))
        except Exception as exc:
            logger.warning("Cache DELETE error [%s]: %s", key, exc)

    @classmethod
    async def exists(cls, key: str) -> bool:
        try:
            r = await get_redis()
            return bool(await r.exists(cls._key(key)))
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Convenience TTL constants (seconds)
# ---------------------------------------------------------------------------


class TTL:
    ONE_MINUTE = 60
    FIVE_MINUTES = 300
    ONE_HOUR = 3600
    ONE_DAY = 86400
    ONE_WEEK = 604800

    # Domain-specific
    CUTOFF_MARKS = ONE_DAY  # change rarely within a cycle
    UNIVERSITIES = ONE_WEEK  # very stable
    CURRENT_CYCLE = ONE_HOUR  # might update during active cycle
    SUBJECT_COMBOS = ONE_WEEK
