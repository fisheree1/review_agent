"""Short-lived Redis counters for costly API actions; PostgreSQL remains authoritative."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from functools import lru_cache
from typing import Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import ApplicationError

Action = Literal["login", "upload", "index", "ask", "quiz", "feedback"]

# The script makes replay detection, quota check, and expiry one atomic operation.
LIMIT_SCRIPT = """
if ARGV[3] == '1' and redis.call('EXISTS', KEYS[2]) == 1 then
    return 1
end
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count >= tonumber(ARGV[2]) then
    return 0
end
count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
if ARGV[3] == '1' then
    redis.call('SET', KEYS[2], '1', 'EX', ARGV[1])
end
return 1
"""

LIMITS: dict[Action, tuple[int, int]] = {
    "login": (20, 15 * 60),
    "upload": (30, 60 * 60),
    "index": (30, 60 * 60),
    "ask": (60, 60 * 60),
    "quiz": (20, 60 * 60),
    "feedback": (120, 60 * 60),
}


class RedisRateLimiter:
    def __init__(self, client: Redis, *, key_secret: bytes) -> None:
        self.client = client
        self.key_secret = key_secret

    def _digest(self, value: str) -> str:
        return hmac.new(self.key_secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    async def allow(
        self,
        *,
        action: Action,
        subject: str,
        idempotency_key: str | None = None,
    ) -> bool:
        maximum, window_seconds = LIMITS[action]
        subject_digest = self._digest(subject)
        bucket = f"limit:{action}:{subject_digest}"
        replay = (
            f"limit:replay:{action}:{subject_digest}:{self._digest(idempotency_key)}"
            if idempotency_key
            else bucket
        )
        result = await self.client.eval(
            LIMIT_SCRIPT,
            2,
            bucket,
            replay,
            window_seconds,
            maximum,
            int(bool(idempotency_key)),
        )
        return bool(result == 1)

    async def close(self) -> None:
        await self.client.aclose()


@lru_cache
def get_rate_limiter() -> RedisRateLimiter | None:
    settings = get_settings()
    if not settings.redis_enabled:
        return None
    password = settings.redis_password.get_secret_value()
    client = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password or None,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        decode_responses=True,
    )
    seed = password.encode("utf-8") or secrets.token_bytes(32)
    key_secret = hmac.new(seed, b"review-agent-rate-limit-v1", hashlib.sha256).digest()
    return RedisRateLimiter(client, key_secret=key_secret)


async def enforce_rate_limit(
    limiter: RedisRateLimiter | None,
    *,
    action: Action,
    subject: str,
    idempotency_key: str | None = None,
) -> None:
    if limiter is None:
        return
    try:
        allowed = await limiter.allow(
            action=action, subject=subject, idempotency_key=idempotency_key
        )
    except RedisError as exc:
        raise ApplicationError(
            code="RATE_LIMIT_UNAVAILABLE",
            message="请求暂时无法处理，请稍后重试",
            status_code=503,
        ) from exc
    if not allowed:
        raise ApplicationError(
            code="RATE_LIMITED",
            message="操作过于频繁，请稍后重试",
            status_code=429,
        )
