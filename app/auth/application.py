from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.auth.domain import normalize_email, validate_password
from app.auth.ports import AuthStore, SessionRecord
from app.core.errors import ApplicationError


class Passwords(Protocol):
    dummy_hash: str

    def hash(self, password: str) -> str: ...
    def verify(self, stored_hash: str, password: str) -> bool: ...


def session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    identity: SessionRecord


class AuthService:
    def __init__(self, store: AuthStore, passwords: Passwords) -> None:
        self.store = store
        self.passwords = passwords

    async def register(self, email: str, password: str) -> UUID:
        normalized = normalize_email(email)
        safe_password = validate_password(password)
        password_hash = await asyncio.to_thread(self.passwords.hash, safe_password)
        user_id, _ = await self.store.create_account(normalized, password_hash)
        return user_id

    async def login(self, email: str, password: str) -> IssuedSession:
        try:
            normalized = normalize_email(email)
        except ApplicationError:
            normalized = "invalid@example.invalid"
        subject_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if not await self.store.login_allowed(subject_hash):
            raise ApplicationError(
                code="LOGIN_RATE_LIMITED", message="尝试过多，请 15 分钟后重试", status_code=429
            )
        account = await self.store.account_for_email(normalized)
        stored_hash = account.password_hash if account else self.passwords.dummy_hash
        verified = await asyncio.to_thread(self.passwords.verify, stored_hash, password)
        if account is None or account.status != "active" or not verified:
            await self.store.record_login_failure(subject_hash)
            raise ApplicationError(
                code="INVALID_CREDENTIALS", message="账号或密码不正确", status_code=401
            )
        await self.store.clear_login_limit(subject_hash)
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        identity = await self.store.create_session(
            account.id, session_digest(token), csrf_token, datetime.now(UTC) + timedelta(hours=24)
        )
        return IssuedSession(token, identity)

    async def current(self, token: str) -> SessionRecord | None:
        if not token or len(token) > 128:
            return None
        try:
            digest = session_digest(token)
        except UnicodeEncodeError:
            return None
        return await self.store.lookup_session(digest)

    async def logout(self, token: str) -> None:
        if token:
            try:
                await self.store.revoke_session(session_digest(token))
            except UnicodeEncodeError:
                pass

    async def change_password(
        self, user_id: UUID, current_password: str, new_password: str
    ) -> None:
        account = await self.store.account_for_user(user_id)
        if account is None or not await asyncio.to_thread(
            self.passwords.verify, account.password_hash, current_password
        ):
            raise ApplicationError(
                code="INVALID_CREDENTIALS", message="当前密码不正确", status_code=401
            )
        password_hash = await asyncio.to_thread(
            self.passwords.hash, validate_password(new_password)
        )
        await self.store.change_password(account.id, password_hash)
