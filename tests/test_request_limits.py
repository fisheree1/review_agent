from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.core.request_limits import RequestBodyLimitMiddleware

MAX_BYTES = 16


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def echo_body_size(scope: Scope, receive: Receive, send: Send) -> None:
    body = await Request(scope, receive).body()
    await JSONResponse({"received": len(body)})(scope, receive, send)


def limited_client() -> httpx.AsyncClient:
    app = RequestBodyLimitMiddleware(
        echo_body_size, path="/limited/", max_bytes=MAX_BYTES, prefix=True
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def chunked(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


@pytest.mark.anyio
async def test_streamed_body_without_content_length_is_rejected_once_over_limit() -> None:
    async with limited_client() as client:
        response = await client.post("/limited/upload", content=chunked(b"x" * (MAX_BYTES + 1)))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"


@pytest.mark.anyio
async def test_understated_content_length_cannot_bypass_limit() -> None:
    async with limited_client() as client:
        request = client.build_request("POST", "/limited/upload", content=b"x" * (MAX_BYTES + 1))
        request.headers["Content-Length"] = "1"
        response = await client.send(request)

    assert response.status_code == 413


@pytest.mark.anyio
async def test_malformed_content_length_is_rejected() -> None:
    async with limited_client() as client:
        request = client.build_request("POST", "/limited/upload", content=b"x")
        request.headers["Content-Length"] = "not-a-number"
        response = await client.send(request)

    assert response.status_code == 413


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "size", "expected"),
    [
        ("/limited/upload", MAX_BYTES, MAX_BYTES),
        ("/other", MAX_BYTES * 4, MAX_BYTES * 4),
    ],
)
async def test_bodies_within_limit_or_outside_scope_pass_through(
    path: str, size: int, expected: int
) -> None:
    async with limited_client() as client:
        response = await client.post(path, content=b"x" * size)

    assert response.status_code == 200
    assert response.json() == {"received": expected}
