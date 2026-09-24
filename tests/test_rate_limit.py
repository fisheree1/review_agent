from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.auth import Principal, get_current_principal
from app.core.errors import ApplicationError
from app.core.rate_limit import RedisRateLimiter, enforce_rate_limit, get_rate_limiter
from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client


@pytest.mark.anyio
async def test_rate_limit_keys_hide_email_and_scope_replays() -> None:
    calls: list[tuple[object, ...]] = []

    async def evaluate(*args: object) -> int:
        calls.append(args)
        return 1

    limiter = RedisRateLimiter(SimpleNamespace(eval=evaluate), key_secret=b"test")  # type: ignore[arg-type]
    assert await limiter.allow(action="login", subject="private@example.com")
    assert await limiter.allow(action="ask", subject="workspace-one", idempotency_key="first")
    assert await limiter.allow(action="ask", subject="workspace-two", idempotency_key="first")
    assert "private@example.com" not in str(calls)
    assert calls[1][3] != calls[2][3]
    assert calls[1][-1] == 1


@pytest.mark.anyio
async def test_limiter_failure_stops_costly_request() -> None:
    async def unavailable(*_: object) -> int:
        raise RedisConnectionError("private Redis endpoint")

    limiter = RedisRateLimiter(SimpleNamespace(eval=unavailable), key_secret=b"test")  # type: ignore[arg-type]
    with pytest.raises(ApplicationError) as caught:
        await enforce_rate_limit(limiter, action="quiz", subject="workspace-one")
    assert caught.value.code == "RATE_LIMIT_UNAVAILABLE"
    assert caught.value.status_code == 503
    assert "private Redis endpoint" not in caught.value.message


@pytest.mark.anyio
async def test_unauthenticated_login_and_authenticated_question_return_429_before_work(
    client: httpx.AsyncClient,
) -> None:
    async def deny(**_: object) -> bool:
        return False

    app.dependency_overrides[get_rate_limiter] = lambda: SimpleNamespace(allow=deny)
    app.dependency_overrides[get_current_principal] = lambda: Principal(workspace_public_id=uuid4())
    try:
        login = await client.post(
            "/api/v1/auth/login", json={"email": "reader@example.com", "password": "secret"}
        )
        question = await client.post(
            f"/api/v1/documents/{uuid4()}/questions",
            json={"question": "What does this document say?"},
            headers={"Idempotency-Key": "test-question"},
        )
    finally:
        app.dependency_overrides.pop(get_rate_limiter, None)
        app.dependency_overrides.pop(get_current_principal, None)
    assert login.status_code == 429
    assert question.status_code == 429
    assert login.json()["error"]["code"] == "RATE_LIMITED"
    assert question.json()["error"]["code"] == "RATE_LIMITED"
