from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import AuthSession, LoginLimit, PasswordCredential, User, WorkspaceMember
from app.auth.ports import AccountRecord, SessionRecord
from app.core.errors import ApplicationError
from app.documents.infrastructure.models import WorkspaceModel


class SqlAuthStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create_account(
        self, email: str, password_hash: str, existing_workspace: UUID | None = None
    ) -> tuple[UUID, UUID]:
        try:
            async with self.sessions.begin() as session:
                if existing_workspace is None:
                    workspace = WorkspaceModel(name="个人学习空间", status="active")
                    session.add(workspace)
                    await session.flush()
                else:
                    existing = await session.scalar(
                        select(WorkspaceModel)
                        .where(
                            WorkspaceModel.public_id == existing_workspace,
                            WorkspaceModel.status == "active",
                        )
                        .with_for_update()
                    )
                    if existing is None:
                        raise ApplicationError(
                            code="WORKSPACE_NOT_FOUND", message="学习空间不存在", status_code=404
                        )
                    workspace = existing
                    owner = await session.scalar(
                        select(WorkspaceMember.id).where(
                            WorkspaceMember.workspace_id == workspace.id,
                            WorkspaceMember.role == "owner",
                        )
                    )
                    if owner is not None:
                        raise ApplicationError(
                            code="WORKSPACE_OWNED", message="学习空间已有所有者", status_code=409
                        )
                user = User(email_normalized=email, status="active")
                session.add(user)
                await session.flush()
                session.add_all(
                    [
                        PasswordCredential(user_id=user.id, password_hash=password_hash),
                        WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="owner"),
                    ]
                )
                await session.flush()
                return user.public_id, workspace.public_id
        except IntegrityError as exc:
            raise ApplicationError(
                code="ACCOUNT_EXISTS", message="该账号已存在", status_code=409
            ) from exc

    async def account_for_email(self, email: str) -> AccountRecord | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(User, PasswordCredential.password_hash)
                    .join(PasswordCredential, PasswordCredential.user_id == User.id)
                    .where(User.email_normalized == email)
                )
            ).one_or_none()
            if row is None:
                return None
            user, password_hash = row
            return AccountRecord(
                user.id, user.public_id, user.email_normalized, password_hash, user.status
            )

    async def account_for_user(self, user_id: UUID) -> AccountRecord | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(User, PasswordCredential.password_hash)
                    .join(PasswordCredential, PasswordCredential.user_id == User.id)
                    .where(User.public_id == user_id)
                )
            ).one_or_none()
            if row is None:
                return None
            user, password_hash = row
            return AccountRecord(
                user.id, user.public_id, user.email_normalized, password_hash, user.status
            )

    async def login_allowed(self, subject_hash: str) -> bool:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            await session.execute(
                insert(LoginLimit)
                .values(
                    subject_hash=subject_hash, failed_count=0, reset_at=now + timedelta(minutes=15)
                )
                .on_conflict_do_nothing(index_elements=["subject_hash"])
            )
            item = await session.scalar(
                select(LoginLimit).where(LoginLimit.subject_hash == subject_hash).with_for_update()
            )
            assert item is not None
            if item.reset_at <= now:
                item.failed_count = 0
                item.reset_at = now + timedelta(minutes=15)
                item.locked_until = None
            return item.locked_until is None or item.locked_until <= now

    async def record_login_failure(self, subject_hash: str) -> None:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            item = await session.scalar(
                select(LoginLimit).where(LoginLimit.subject_hash == subject_hash).with_for_update()
            )
            if item is None:
                return
            item.failed_count += 1
            if item.failed_count >= 5:
                item.locked_until = now + timedelta(minutes=15)

    async def clear_login_limit(self, subject_hash: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(delete(LoginLimit).where(LoginLimit.subject_hash == subject_hash))

    async def create_session(
        self, user_id: int, session_hash: str, csrf_token: str, expires_at: datetime
    ) -> SessionRecord:
        async with self.sessions.begin() as session:
            row = (
                await session.execute(
                    select(User, WorkspaceModel)
                    .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                    .join(WorkspaceModel, WorkspaceModel.id == WorkspaceMember.workspace_id)
                    .where(
                        User.id == user_id,
                        User.status == "active",
                        WorkspaceModel.status == "active",
                    )
                    .order_by(WorkspaceMember.id)
                    .limit(1)
                )
            ).one_or_none()
            if row is None:
                raise ApplicationError(
                    code="AUTHENTICATION_REQUIRED", message="账号不可用", status_code=401
                )
            user, workspace = row
            session.add(
                AuthSession(
                    session_hash=session_hash,
                    csrf_token=csrf_token,
                    user_id=user.id,
                    workspace_id=workspace.id,
                    expires_at=expires_at,
                )
            )
            return SessionRecord(
                user.public_id, workspace.public_id, user.email_normalized, csrf_token
            )

    async def lookup_session(self, session_hash: str) -> SessionRecord | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(AuthSession, User, WorkspaceModel)
                    .join(User, User.id == AuthSession.user_id)
                    .join(
                        WorkspaceMember,
                        (WorkspaceMember.user_id == AuthSession.user_id)
                        & (WorkspaceMember.workspace_id == AuthSession.workspace_id),
                    )
                    .join(WorkspaceModel, WorkspaceModel.id == AuthSession.workspace_id)
                    .where(
                        AuthSession.session_hash == session_hash,
                        AuthSession.revoked_at.is_(None),
                        AuthSession.expires_at > datetime.now(UTC),
                        User.status == "active",
                        WorkspaceModel.status == "active",
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            auth_session, user, workspace = row
            return SessionRecord(
                user.public_id, workspace.public_id, user.email_normalized, auth_session.csrf_token
            )

    async def revoke_session(self, session_hash: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(AuthSession)
                .where(AuthSession.session_hash == session_hash, AuthSession.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            )

    async def change_password(self, user_id: int, password_hash: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(PasswordCredential)
                .where(PasswordCredential.user_id == user_id)
                .values(password_hash=password_hash, changed_at=datetime.now(UTC))
            )
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            )
