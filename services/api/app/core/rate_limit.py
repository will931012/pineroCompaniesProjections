import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.metrics import RATE_LIMITED_REQUESTS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult: ...


class MemoryRateLimiter:
    """Fixed-window limiter for a single process. Used in tests and as a Redis fallback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = int(time.time())
        window = now // window_seconds
        with self._lock:
            current_window, count = self._windows.get(key, (window, 0))
            if current_window != window:
                count = 0
            count += 1
            self._windows[key] = (window, count)
        retry_after = window_seconds - (now % window_seconds)
        return RateLimitResult(count <= limit, max(limit - count, 0), retry_after)

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


class RedisRateLimiter:
    """Fixed-window limiter shared by every API process. Falls back to memory if Redis fails."""

    def __init__(self, client: Redis, fallback: MemoryRateLimiter) -> None:
        self._client = client
        self._fallback = fallback

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        window = int(time.time()) // window_seconds
        redis_key = f"pinero:ratelimit:{key}:{window}"
        try:
            pipeline = self._client.pipeline()
            pipeline.incr(redis_key)
            pipeline.expire(redis_key, window_seconds, nx=True)
            pipeline.ttl(redis_key)
            count, _, ttl = pipeline.execute()
        except RedisError:
            logger.warning("rate_limit_redis_unavailable_using_memory_fallback")
            return self._fallback.hit(key, limit, window_seconds)
        return RateLimitResult(count <= limit, max(limit - count, 0), max(int(ttl), 1))


@lru_cache
def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    memory = MemoryRateLimiter()
    if settings.rate_limit_backend == "memory":
        return memory
    client = Redis.from_url(settings.redis_url, socket_timeout=0.5, socket_connect_timeout=0.5)
    return RedisRateLimiter(client, memory)


def enforce_rate_limit(scope: str, identity: str, limit: int, window_seconds: int = 60) -> None:
    result = get_rate_limiter().hit(f"{scope}:{identity}", limit, window_seconds)
    if not result.allowed:
        RATE_LIMITED_REQUESTS.labels(scope=scope).inc()
        raise ApiError(
            429,
            "rate_limited",
            "Too many requests. Try again later.",
            headers={"Retry-After": str(result.retry_after_seconds)},
        )
