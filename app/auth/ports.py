from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AccountRecord:
    id: int
    public_id: UUID
    email: str
    password_hash: str
    status: str


@dataclass(frozen=True, slots=True)
class SessionRecord:
    user_id: UUID
    workspace_id: UUID
    email: str
    csrf_token: str


class AuthStore(Protocol):
    async def create_account(
        self, email: str, password_hash: str, existing_workspace: UUID | None = None
    ) -> tuple[UUID, UUID]: ...

    async def account_for_email(self, email: str) -> AccountRecord | None: ...
    async def account_for_user(self, user_id: UUID) -> AccountRecord | None: ...
    async def login_allowed(self, subject_hash: str) -> bool: ...
    async def record_login_failure(self, subject_hash: str) -> None: ...
    async def clear_login_limit(self, subject_hash: str) -> None: ...
    async def create_session(
        self, user_id: int, session_hash: str, csrf_token: str, expires_at: datetime
    ) -> SessionRecord: ...
    async def lookup_session(self, session_hash: str) -> SessionRecord | None: ...
    async def revoke_session(self, session_hash: str) -> None: ...
    async def change_password(self, user_id: int, password_hash: str) -> None: ...
