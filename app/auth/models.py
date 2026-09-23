from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database_schema import APPLICATION_SCHEMA as S
from app.core.database_schema import AUTH_SCHEMA as A
from app.core.models import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active','suspended')", name="ck_users_status"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), unique=True, default=uuid4
    )
    email_normalized: Mapped[str] = mapped_column(String(254), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_workspace_members_user_workspace"),
        Index("ix_workspace_members_workspace", "workspace_id"),
        CheckConstraint("role IN ('owner','member')", name="ck_workspace_members_role"),
        ForeignKeyConstraint(["workspace_id"], [f"{S}.workspaces.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["user_id"], [f"{S}.users.id"], ondelete="CASCADE"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(20), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PasswordCredential(Base):
    __tablename__ = "password_credentials"
    __table_args__ = ({"schema": A},)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{S}.users.id", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(String(300))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "workspace_id"],
            [f"{S}.workspace_members.user_id", f"{S}.workspace_members.workspace_id"],
            ondelete="CASCADE",
        ),
        Index("ix_auth_sessions_user", "user_id", "expires_at"),
        Index("ix_auth_sessions_expiry", "expires_at"),
        {"schema": A},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(BigInteger)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginLimit(Base):
    __tablename__ = "login_limits"
    __table_args__ = ({"schema": A},)
    subject_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failed_count: Mapped[int] = mapped_column(Integer)
    reset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
