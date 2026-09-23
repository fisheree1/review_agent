"""Add users, workspace membership, credentials, and revocable sessions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from app.core.database_schema import APPLICATION_SCHEMA as S
from app.core.database_schema import AUTH_SCHEMA as A

revision: str = "0010_user_auth"
down_revision: str | None = "0009_learning_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("public_id", UUID(), nullable=False, unique=True),
        sa.Column("email_normalized", sa.String(254), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('active','suspended')", name="ck_users_status"),
        schema=S,
    )
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_workspace_members_user_workspace"),
        sa.CheckConstraint("role IN ('owner','member')", name="ck_workspace_members_role"),
        sa.ForeignKeyConstraint(["workspace_id"], [f"{S}.workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], [f"{S}.users.id"], ondelete="CASCADE"),
        schema=S,
    )
    op.create_index(
        "ix_workspace_members_workspace", "workspace_members", ["workspace_id"], schema=S
    )
    op.create_table(
        "password_credentials",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], [f"{S}.users.id"], ondelete="CASCADE"),
        schema=A,
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("session_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id", "workspace_id"],
            [f"{S}.workspace_members.user_id", f"{S}.workspace_members.workspace_id"],
            ondelete="CASCADE",
        ),
        schema=A,
    )
    op.create_index("ix_auth_sessions_user", "sessions", ["user_id", "expires_at"], schema=A)
    op.create_index("ix_auth_sessions_expiry", "sessions", ["expires_at"], schema=A)
    op.create_table(
        "login_limits",
        sa.Column("subject_hash", sa.String(64), primary_key=True),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        schema=A,
    )


def downgrade() -> None:
    # Destructive for identity records; production rollback must roll forward instead.
    op.drop_table("login_limits", schema=A)
    op.drop_table("sessions", schema=A)
    op.drop_table("password_credentials", schema=A)
    op.drop_table("workspace_members", schema=S)
    op.drop_table("users", schema=S)
