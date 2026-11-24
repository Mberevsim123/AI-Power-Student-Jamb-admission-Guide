"""
app/core/rate_limit.py
Sliding-window rate limiter backed by Redis.
Applied as a FastAPI dependency.
"""

import logging
import time
from uuid import uuid4

from fastapi import HTTPException, Request, status

from app.utils.cache import get_redis

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Usage:
        chat_limiter = RateLimiter(requests=20, window=60)   # 20 req/min

        @router.post("/")
        async def endpoint(request: Request, _=Depends(chat_limiter)):
            ...
    """

    def __init__(self, requests: int, window: int):
        self.requests = requests  # max allowed in window
        self.window = window  # seconds

    async def __call__(self, request: Request) -> None:
        identifier = self._get_identifier(request)
        key = f"rl:{identifier}:{request.url.path}"

        try:
            allowed = await self._check(key)
        except Exception as exc:
            logger.warning("Rate-limiter Redis error: %s — allowing request", exc)
            return  # fail open: don't block on Redis failure

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Max {self.requests} requests per {self.window}s.",
                headers={"Retry-After": str(self.window)},
            )

    async def _check(self, key: str) -> bool:
        r = await get_redis()
        now = time.time()

        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, now - self.window)
        pipe.zadd(key, {str(uuid4()): now})
        pipe.zcard(key)
        pipe.expire(key, self.window)
        results = await pipe.execute()

        count = results[2]
        return count <= self.requests

    @staticmethod
    def _get_identifier(request: Request) -> str:
        # Prefer authenticated user id if available
        user = getattr(request.state, "user", None)
        if user:
            return f"user:{user.id}"
        return f"ip:{request.client.host if request.client else 'unknown'}"


chat_limiter = RateLimiter(requests=30, window=60)
auth_limiter = RateLimiter(requests=10, window=60)
search_limiter = RateLimiter(requests=60, window=60)
