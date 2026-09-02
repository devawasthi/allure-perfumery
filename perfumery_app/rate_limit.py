from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import defaultdict, deque

try:
    import redis
except ImportError:  # pragma: no cover - dependencies are installed in deployment
    redis = None


logger = logging.getLogger("the_scentist.rate_limit")


class RateLimiter:
    """Redis-backed fixed windows with a bounded local development fallback."""

    def __init__(self, redis_url: str):
        self._configured = bool(redis_url)
        self._client = redis.Redis.from_url(redis_url, decode_responses=True) if redis_url and redis else None
        self._local: dict[str, deque[float]] = defaultdict(deque)
        self._local_locks: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._redis_failed_at = 0.0

    def is_ready(self) -> bool:
        if not self._configured:
            return True
        if self._client is None:
            return False
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def check(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        if self._client is not None and time.monotonic() - self._redis_failed_at > 30:
            try:
                bucket = int(time.time()) // window_seconds
                redis_key = f"the-scentist:rate:{key}:{bucket}"
                pipe = self._client.pipeline()
                pipe.incr(redis_key)
                pipe.expire(redis_key, window_seconds + 2)
                count, _ = pipe.execute()
                retry_after = window_seconds - (int(time.time()) % window_seconds)
                return int(count) <= limit, max(1, retry_after)
            except Exception:
                self._redis_failed_at = time.monotonic()
                logger.exception("Redis rate limiter unavailable; using process-local fallback")

        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            entries = self._local[key]
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= limit:
                return False, max(1, int(window_seconds - (now - entries[0])))
            entries.append(now)
            if len(self._local) > 10_000:
                for stale_key in [item for item, values in self._local.items() if not values or values[-1] <= cutoff][
                    :2_000
                ]:
                    self._local.pop(stale_key, None)
        return True, 0

    def acquire_lock(self, key: str, ttl_seconds: int = 30) -> str | None:
        token = secrets.token_urlsafe(18)
        redis_key = f"the-scentist:lock:{key}"
        if self._client is not None and time.monotonic() - self._redis_failed_at > 30:
            try:
                return token if self._client.set(redis_key, token, nx=True, ex=ttl_seconds) else None
            except Exception:
                self._redis_failed_at = time.monotonic()
                logger.exception("Redis checkout lock unavailable; using process-local fallback")
        now = time.monotonic()
        with self._lock:
            current = self._local_locks.get(key)
            if current and current[1] > now:
                return None
            self._local_locks[key] = (token, now + ttl_seconds)
        return token

    def release_lock(self, key: str, token: str) -> None:
        redis_key = f"the-scentist:lock:{key}"
        if self._client is not None:
            try:
                self._client.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    redis_key,
                    token,
                )
                return
            except Exception:
                logger.exception("Unable to release Redis checkout lock")
        with self._lock:
            if self._local_locks.get(key, (None, 0))[0] == token:
                self._local_locks.pop(key, None)
