from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth.application import IssuedSession
from app.auth.dependencies import get_auth_service
from app.auth.ports import SessionRecord
from app.core.config import Settings, get_settings
from app.main import app


class GatewaySessions:
    def __init__(self) -> None:
        self.identity = SessionRecord(uuid4(), uuid4(), "portal@example.test", "csrf-fixture")
        self.issued = 0

    async def current(self, token: str) -> SessionRecord | None:
        return self.identity if token == "session-fixture" else None

    async def trusted_gateway_session(self, user_id: object) -> IssuedSession:
        assert user_id == self.identity.user_id
        self.issued += 1
        return IssuedSession("session-fixture", self.identity)


@pytest.fixture
def gateway() -> Iterator[tuple[TestClient, GatewaySessions]]:
    service = GatewaySessions()
    settings = Settings(
        _env_file=None,
        postgres_runtime_password=SecretStr("database-fixture"),
        storage_access_key="storage-fixture",
        storage_secret_key=SecretStr("storage-fixture"),
        app_env="production",
        auth_public_origin="https://fisher-ai.com",
        redis_enabled=True,
        redis_password=SecretStr("redis-fixture"),
        auth_gateway_enabled=True,
        auth_gateway_token=SecretStr("gateway-fixture-" * 4),
        auth_gateway_user_id=service.identity.user_id,
        auth_cookie_path="/review/",
    )
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_auth_service] = lambda: service
    client = TestClient(app, base_url="https://fisher-ai.com")
    try:
        yield client, service
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_missing_or_forged_gateway_proof_cannot_create_a_session(
    gateway: tuple[TestClient, GatewaySessions],
) -> None:
    client, service = gateway
    for headers in ({}, {"X-Review-Gateway": "forged"}):
        response = client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 401
        assert "set-cookie" not in response.headers
    assert service.issued == 0


def test_verified_portal_creates_a_scoped_secure_session(
    gateway: tuple[TestClient, GatewaySessions],
) -> None:
    client, service = gateway
    response = client.get("/api/v1/auth/me", headers={"X-Review-Gateway": "gateway-fixture-" * 4})
    assert response.status_code == 200
    assert response.json()["user_id"] == str(service.identity.user_id)
    assert response.json()["workspace_id"] == str(service.identity.workspace_id)
    assert response.json()["login_mode"] == "portal"
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "Path=/review/" in cookie
    assert service.issued == 1


def test_gateway_sessions_keep_csrf_and_cannot_switch_to_another_owner(
    gateway: tuple[TestClient, GatewaySessions],
) -> None:
    client, service = gateway
    headers = {
        "X-Review-Gateway": "gateway-fixture-" * 4,
        "Cookie": "__Secure-review_agent_session=session-fixture",
    }
    response = client.post("/api/v1/auth/logout", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"
    service.identity = SessionRecord(uuid4(), uuid4(), "other@example.test", "other-csrf")
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert service.issued == 0
    response = client.get(
        "/api/v1/auth/me", headers={"Cookie": "__Secure-review_agent_session=session-fixture"}
    )
    assert response.status_code == 401


def test_gateway_cannot_be_bypassed_with_password_login(
    gateway: tuple[TestClient, GatewaySessions],
) -> None:
    client, service = gateway
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "portal@example.test", "password": "unused-fixture"},
        headers={"Origin": "https://fisher-ai.com"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_MANAGED_BY_PORTAL"
    assert service.issued == 0
