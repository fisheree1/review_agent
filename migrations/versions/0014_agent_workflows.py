"""Add owned learning runs, call checkpoints and optimistic answer revisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0014_agent_workflows"
down_revision: str | None = "0013_conversation_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_quiz_attempts_quiz_scope", "quiz_attempts", ["id", "quiz_id", "workspace_id"], schema=S
    )
    op.add_column(
        "quiz_attempts",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        schema=S,
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("public_id", UUID(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("graph_version", sa.String(40), nullable=False),
        sa.Column("profile", sa.String(160), nullable=False),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan", JSONB(), nullable=True),
        sa.Column("usage", JSONB(), nullable=False, server_default="{}"),
        sa.Column("outputs", JSONB(), nullable=False, server_default="[]"),
        sa.Column("quiz_id", sa.BigInteger(), nullable=True),
        sa.Column("attempt_id", sa.BigInteger(), nullable=True),
        sa.Column("quiz_reserved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("clarification", sa.String(600), nullable=True),
        sa.Column("response", sa.String(1200), nullable=True),
        sa.Column("response_key", sa.String(200), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("fence", UUID(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("id", "workspace_id", name="uq_agent_runs_scope"),
        sa.UniqueConstraint("message_id", "workspace_id", name="uq_agent_runs_message"),
        sa.UniqueConstraint("attempt_id", "workspace_id", name="uq_agent_runs_attempt"),
        sa.ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            [f"{S}.conversation_messages.id", f"{S}.conversation_messages.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{S}.conversations.id", f"{S}.conversations.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quiz_id", "workspace_id"],
            [f"{S}.quizzes.id", f"{S}.quizzes.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "quiz_id", "workspace_id"],
            [
                f"{S}.quiz_attempts.id",
                f"{S}.quiz_attempts.quiz_id",
                f"{S}.quiz_attempts.workspace_id",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','waiting_input','waiting_result',"
            "'blocked','completed','failed','cancelled','expired')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_agent_runs_revision"),
        schema=S,
    )
    op.create_index("ix_agent_runs_claim", "agent_runs", ["status", "created_at"], schema=S)
    op.create_index(
        "ix_agent_runs_conversation",
        "agent_runs",
        ["workspace_id", "conversation_id", "id"],
        schema=S,
    )
    op.create_index("ix_agent_runs_attempt", "agent_runs", ["workspace_id", "attempt_id"], schema=S)
    op.create_table(
        "agent_stage_executions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("usage", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            [f"{S}.agent_runs.id", f"{S}.agent_runs.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("run_id", "stage", "ordinal", name="uq_agent_stage_call"),
        sa.CheckConstraint(
            "status IN ('calling','returned','published')", name="ck_agent_stage_status"
        ),
        sa.CheckConstraint("ordinal >= 0 AND ordinal < 12", name="ck_agent_stage_ordinal"),
        schema=S,
    )


def downgrade() -> None:
    op.drop_table("agent_stage_executions", schema=S)
    op.drop_table("agent_runs", schema=S)
    op.drop_constraint("uq_quiz_attempts_quiz_scope", "quiz_attempts", type_="unique", schema=S)
    op.drop_column("quiz_attempts", "revision", schema=S)
