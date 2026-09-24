"""Exercise Redis ACL access, quota expiry setup, and idempotent replay."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.core.rate_limit import LIMITS, get_rate_limiter


async def verify() -> None:
    limiter = get_rate_limiter()
    if limiter is None:
        raise RuntimeError("Redis rate limiting is disabled")
    subject = f"redis-smoke-{uuid4()}"
    try:
        if not await limiter.client.ping():
            raise RuntimeError("Redis did not respond to PING")
        maximum, _ = LIMITS["quiz"]
        for index in range(maximum):
            if not await limiter.allow(
                action="quiz", subject=subject, idempotency_key=f"request-{index}"
            ):
                raise RuntimeError("Redis rejected a request before the quota")
        if await limiter.allow(action="quiz", subject=subject, idempotency_key="over-quota"):
            raise RuntimeError("Redis accepted a request above the quota")
        if not await limiter.allow(action="quiz", subject=subject, idempotency_key="request-0"):
            raise RuntimeError("Redis rejected an idempotent replay")
    finally:
        await limiter.close()


def main() -> None:
    asyncio.run(verify())
    print("Redis rate limiting and idempotent replay passed.")


if __name__ == "__main__":
    main()
