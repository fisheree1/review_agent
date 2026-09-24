from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest

from app.core.auth import get_current_principal
from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.anyio
async def test_liveness(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


@pytest.mark.anyio
async def test_root(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.json()["docs"] == "/docs"


@pytest.mark.anyio
async def test_document_endpoint_requires_bearer_token(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/documents/{uuid4()}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.anyio
async def test_unexpected_error_never_returns_private_exception_text(
    client: httpx.AsyncClient, capfd: pytest.CaptureFixture[str]
) -> None:
    def broken_auth() -> None:
        raise RuntimeError("private document text and credentials")

    app.dependency_overrides[get_current_principal] = broken_auth
    try:
        response = await client.get(f"/api/v1/documents/{uuid4()}")
    finally:
        app.dependency_overrides.pop(get_current_principal, None)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert "private document" not in response.text
    assert "private document" not in capfd.readouterr().out


@pytest.mark.anyio
async def test_document_upload_rejects_oversized_request_before_form_parsing(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/documents",
        content=b"too large",
        headers={"Content-Length": str(27 * 1024 * 1024)},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"
