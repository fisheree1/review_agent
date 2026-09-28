"""Add conversation folders and member-owned daily check-ins without backfilling history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0015_learning_organization"
down_revision: str | None = "0014_agent_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_groups",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("public_id", UUID(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("public_id", "workspace_id", name="uq_conversation_groups_scope"),
        sa.ForeignKeyConstraint(["workspace_id"], [f"{S}.workspaces.id"], ondelete="CASCADE"),
        schema=S,
    )
    op.create_index(
        "ix_conversation_groups_workspace",
        "conversation_groups",
        ["workspace_id", "created_at"],
        schema=S,
    )
    op.add_column("conversations", sa.Column("group_id", UUID(), nullable=True), schema=S)
    op.add_column("conversations", sa.Column("create_key", sa.String(200), nullable=True), schema=S)
    op.add_column("conversations", sa.Column("create_request", JSONB(), nullable=True), schema=S)
    op.create_unique_constraint(
        "uq_conversations_create_key", "conversations", ["workspace_id", "create_key"], schema=S
    )
    op.create_foreign_key(
        "fk_conversations_group_id_workspace_id_conversation_groups",
        "conversations",
        "conversation_groups",
        ["group_id", "workspace_id"],
        ["public_id", "workspace_id"],
        source_schema=S,
        referent_schema=S,
    )
    op.create_index(
        "ix_conversations_group", "conversations", ["workspace_id", "group_id"], schema=S
    )
    op.create_table(
        "study_checkins",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "user_id", "local_date", name="uq_study_checkins_day"),
        sa.ForeignKeyConstraint(
            ["user_id", "workspace_id"],
            [f"{S}.workspace_members.user_id", f"{S}.workspace_members.workspace_id"],
            ondelete="CASCADE",
        ),
        schema=S,
    )


def downgrade() -> None:
    # Destructive; use only for disposable databases. Application rollback retains expanded schema.
    op.drop_table("study_checkins", schema=S)
    op.drop_index("ix_conversations_group", table_name="conversations", schema=S)
    op.drop_constraint(
        "fk_conversations_group_id_workspace_id_conversation_groups",
        "conversations",
        schema=S,
        type_="foreignkey",
    )
    op.drop_constraint("uq_conversations_create_key", "conversations", schema=S, type_="unique")
    op.drop_column("conversations", "create_request", schema=S)
    op.drop_column("conversations", "create_key", schema=S)
    op.drop_column("conversations", "group_id", schema=S)
    op.drop_table("conversation_groups", schema=S)
