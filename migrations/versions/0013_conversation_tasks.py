"""Add conversation task results with workspace-constrained Quiz references."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0013_conversation_tasks"
down_revision: str | None = "0012_quiz_agent_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages", sa.Column("task_result", JSONB(), nullable=True), schema=S
    )
    op.add_column(
        "conversation_messages", sa.Column("quiz_id", sa.BigInteger(), nullable=True), schema=S
    )
    op.create_foreign_key(
        "fk_conversation_message_quiz_scope",
        "conversation_messages",
        "quizzes",
        ["quiz_id", "workspace_id"],
        ["id", "workspace_id"],
        source_schema=S,
        referent_schema=S,
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_conversation_messages_quiz",
        "conversation_messages",
        ["workspace_id", "quiz_id"],
        schema=S,
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_quiz", table_name="conversation_messages", schema=S)
    op.drop_constraint(
        "fk_conversation_message_quiz_scope", "conversation_messages", type_="foreignkey", schema=S
    )
    op.drop_column("conversation_messages", "quiz_id", schema=S)
    op.drop_column("conversation_messages", "task_result", schema=S)
