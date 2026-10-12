"""Add personal, idempotent spaced review without changing shared Quiz attempts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0017_spaced_review"
down_revision: str | None = "0016_conversation_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_cards",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "due_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("stage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lapses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "policy_version", sa.String(40), nullable=False, server_default="spaced-review-v1"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "question_id", name="uq_review_card_question"
        ),
        sa.UniqueConstraint("id", "workspace_id", "user_id", name="uq_review_card_owner"),
        sa.ForeignKeyConstraint(
            ["question_id", "workspace_id"],
            [f"{S}.quiz_questions.id", f"{S}.quiz_questions.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "workspace_id"],
            [f"{S}.workspace_members.user_id", f"{S}.workspace_members.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "stage BETWEEN 0 AND 6 AND interval_days BETWEEN 0 AND 120 "
            "AND revision >= 0 AND lapses >= 0",
            name="ck_review_card_schedule",
        ),
        schema=S,
    )
    op.create_index(
        "ix_review_card_due", "review_cards", ["workspace_id", "user_id", "due_at", "id"], schema=S
    )
    op.create_index(
        "ix_review_card_question", "review_cards", ["question_id", "workspace_id"], schema=S
    )
    op.create_table(
        "review_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("rating", sa.String(10), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "idempotency_key", name="uq_review_event_key"
        ),
        sa.ForeignKeyConstraint(
            ["card_id", "workspace_id", "user_id"],
            [f"{S}.review_cards.id", f"{S}.review_cards.workspace_id", f"{S}.review_cards.user_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "rating IN ('again','hard','good') AND expected_revision >= 0",
            name="ck_review_event_rating",
        ),
        schema=S,
    )
    op.create_index(
        "ix_review_event_card", "review_events", ["card_id", "workspace_id", "user_id"], schema=S
    )


def downgrade() -> None:
    op.drop_table("review_events", schema=S)
    op.drop_table("review_cards", schema=S)
