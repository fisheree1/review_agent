"""Exercise account sessions and workspace isolation against an isolated migrated database."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.auth.passwords import Argon2Passwords
from app.auth.store import SqlAuthStore
from app.core.config import get_settings
from app.core.database import async_session_factory, close_database
from app.main import app


async def verify() -> None:
    settings = get_settings()
    store = SqlAuthStore(async_session_factory)
    password = "correct horse battery staple"
    suffix = uuid4().hex
    owner_email = f"owner-{suffix}@example.test"
    other_email = f"other-{suffix}@example.test"
    password_hash = await asyncio.to_thread(Argon2Passwords().hash, password)
    await store.create_account(owner_email, password_hash)
    await store.create_account(other_email, password_hash)

    async with (
        AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.auth_public_origin
        ) as owner,
        AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.auth_public_origin
        ) as other,
    ):
        login = await owner.post(
            "/api/v1/auth/login",
            json={"email": owner_email, "password": password},
            headers={"Origin": settings.auth_public_origin},
        )
        assert login.status_code == 200, login.text
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "samesite=strict" in login.headers["set-cookie"].lower()
        csrf = login.json()["csrf_token"]
        assert (await owner.get("/api/v1/auth/me")).status_code == 200
        without_csrf = await owner.post("/api/v1/collections", json={"name": "Private"})
        assert without_csrf.status_code == 403
        created = await owner.post(
            "/api/v1/collections", json={"name": "Private"}, headers={"X-CSRF-Token": csrf}
        )
        assert created.status_code == 201, created.text
        collection_id = created.json()["id"]

        wrong_origin = await other.post(
            "/api/v1/auth/login",
            json={"email": other_email, "password": password},
            headers={"Origin": "https://untrusted.example"},
        )
        assert wrong_origin.status_code == 403
        closed_signup = await other.post(
            "/api/v1/auth/register",
            json={"email": f"new-{suffix}@example.test", "password": password},
            headers={"Origin": settings.auth_public_origin},
        )
        assert closed_signup.status_code == 403
        signed_in = await other.post(
            "/api/v1/auth/login",
            json={"email": other_email, "password": password},
            headers={"Origin": settings.auth_public_origin},
        )
        assert signed_in.status_code == 200, signed_in.text
        foreign = await other.put(
            f"/api/v1/collections/{collection_id}/documents",
            json={"document_ids": []},
            headers={"X-CSRF-Token": signed_in.json()["csrf_token"]},
        )
        assert foreign.status_code == 404, foreign.text

        changed = await owner.post(
            "/api/v1/auth/change-password",
            json={"current_password": password, "new_password": "a fresh twelve character secret"},
            headers={"X-CSRF-Token": csrf},
        )
        assert changed.status_code == 204, changed.text
        assert (await owner.get("/api/v1/auth/me")).status_code == 401
        old = await owner.post(
            "/api/v1/auth/login",
            json={"email": owner_email, "password": password},
            headers={"Origin": settings.auth_public_origin},
        )
        assert old.status_code == 401
        new = await owner.post(
            "/api/v1/auth/login",
            json={"email": owner_email, "password": "a fresh twelve character secret"},
            headers={"Origin": settings.auth_public_origin},
        )
        assert new.status_code == 200, new.text
        signed_out = await owner.post(
            "/api/v1/auth/logout", headers={"X-CSRF-Token": new.json()["csrf_token"]}
        )
        assert signed_out.status_code == 204
        assert (await owner.get("/api/v1/auth/me")).status_code == 401

        unknown = f"unknown-{suffix}@example.test"
        for _ in range(5):
            bad = await other.post(
                "/api/v1/auth/login",
                json={"email": unknown, "password": "wrong password"},
                headers={"Origin": settings.auth_public_origin},
            )
            assert bad.status_code == 401
        limited = await other.post(
            "/api/v1/auth/login",
            json={"email": unknown, "password": "wrong password"},
            headers={"Origin": settings.auth_public_origin},
        )
        assert limited.status_code == 429

    print("Authentication, CSRF, rate limit, password rotation, and isolation passed.")


async def main() -> None:
    try:
        await verify()
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
