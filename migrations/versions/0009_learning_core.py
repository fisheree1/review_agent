"""Add scoped collections, conversations, feedback, and quiz records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0009_learning_core"
down_revision: str | None = "0008_cited_rag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def pk() -> sa.Column[int]:
    return sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True)


def public_id() -> sa.Column[UUID]:
    return sa.Column("public_id", UUID(), nullable=False, unique=True)


def workspace() -> sa.Column[int]:
    return sa.Column("workspace_id", sa.BigInteger(), nullable=False)


def created() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.create_table(
        "collections",
        pk(),
        public_id(),
        workspace(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        created(),
        sa.UniqueConstraint("id", "workspace_id", name="uq_collections_scope"),
        sa.ForeignKeyConstraint(["workspace_id"], [f"{S}.workspaces.id"], ondelete="CASCADE"),
        schema=S,
    )
    op.create_index(
        "ix_collections_workspace", "collections", ["workspace_id", "created_at"], schema=S
    )
    op.create_table(
        "collection_documents",
        pk(),
        workspace(),
        sa.Column("collection_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("collection_id", "document_id", name="uq_collection_documents_pair"),
        sa.ForeignKeyConstraint(
            ["collection_id", "workspace_id"],
            [f"{S}.collections.id", f"{S}.collections.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "workspace_id"],
            [f"{S}.documents.id", f"{S}.documents.workspace_id"],
            ondelete="CASCADE",
        ),
        schema=S,
    )
    op.create_index(
        "ix_collection_documents_document",
        "collection_documents",
        ["workspace_id", "document_id"],
        schema=S,
    )
    op.create_table(
        "conversations",
        pk(),
        public_id(),
        workspace(),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("scope", JSONB(), nullable=False),
        created(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("id", "workspace_id", name="uq_conversations_scope"),
        sa.ForeignKeyConstraint(["workspace_id"], [f"{S}.workspaces.id"], ondelete="CASCADE"),
        schema=S,
    )
    op.create_index(
        "ix_conversations_workspace", "conversations", ["workspace_id", "created_at"], schema=S
    )
    op.create_table(
        "conversation_documents",
        pk(),
        workspace(),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("document_version_id", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "conversation_id", "document_version_id", name="uq_conversation_documents_pair"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{S}.conversations.id", f"{S}.conversations.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            [f"{S}.document_versions.id", f"{S}.document_versions.workspace_id"],
            ondelete="CASCADE",
        ),
        schema=S,
    )
    op.create_index(
        "ix_conversation_documents_version",
        "conversation_documents",
        ["workspace_id", "document_version_id"],
        schema=S,
    )
    op.create_table(
        "conversation_messages",
        pk(),
        public_id(),
        workspace(),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("question", sa.String(2000), nullable=False),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("profile", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("answer", JSONB()),
        sa.Column("usage", JSONB()),
        sa.Column("fence", UUID()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(64)),
        created(),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_conversation_messages_key"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_conversation_messages_scope"),
        sa.ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{S}.conversations.id", f"{S}.conversations.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued','processing','answered','insufficient','failed','cancelled')",
            name="ck_conversation_messages_status",
        ),
        schema=S,
    )
    op.create_index(
        "ix_conversation_messages_claim",
        "conversation_messages",
        ["status", "created_at"],
        schema=S,
    )
    op.create_index(
        "ix_conversation_messages_history",
        "conversation_messages",
        ["workspace_id", "conversation_id", "created_at"],
        schema=S,
    )
    op.create_table(
        "answer_feedback",
        pk(),
        public_id(),
        workspace(),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("rating", sa.String(24), nullable=False),
        created(),
        sa.UniqueConstraint("workspace_id", "message_id", name="uq_answer_feedback_message"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_answer_feedback_key"),
        sa.ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            [f"{S}.conversation_messages.id", f"{S}.conversation_messages.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "rating IN ('helpful','unhelpful','citation_inaccurate')",
            name="ck_answer_feedback_rating",
        ),
        schema=S,
    )
    op.create_table(
        "quizzes",
        pk(),
        public_id(),
        workspace(),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("requested_scope", JSONB(), nullable=False),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("profile", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("fence", UUID()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        created(),
        sa.UniqueConstraint("id", "workspace_id", name="uq_quizzes_scope"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_quizzes_key"),
        sa.ForeignKeyConstraint(["workspace_id"], [f"{S}.workspaces.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('queued','processing','ready','failed')", name="ck_quizzes_status"
        ),
        schema=S,
    )
    op.create_index("ix_quizzes_claim", "quizzes", ["status", "created_at"], schema=S)
    op.create_index("ix_quizzes_workspace", "quizzes", ["workspace_id", "created_at"], schema=S)
    op.create_table(
        "quiz_documents",
        pk(),
        workspace(),
        sa.Column("quiz_id", sa.BigInteger(), nullable=False),
        sa.Column("document_version_id", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("quiz_id", "document_version_id", name="uq_quiz_documents_pair"),
        sa.ForeignKeyConstraint(
            ["quiz_id", "workspace_id"],
            [f"{S}.quizzes.id", f"{S}.quizzes.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            [f"{S}.document_versions.id", f"{S}.document_versions.workspace_id"],
            ondelete="CASCADE",
        ),
        schema=S,
    )
    op.create_index(
        "ix_quiz_documents_version",
        "quiz_documents",
        ["workspace_id", "document_version_id"],
        schema=S,
    )
    op.create_table(
        "quiz_questions",
        pk(),
        public_id(),
        workspace(),
        sa.Column("quiz_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("difficulty", sa.String(12), nullable=False),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("options", JSONB(), nullable=False),
        sa.Column("answer", JSONB(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("sources", JSONB(), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.UniqueConstraint("quiz_id", "ordinal", name="uq_quiz_questions_ordinal"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_quiz_questions_scope"),
        sa.ForeignKeyConstraint(
            ["quiz_id", "workspace_id"],
            [f"{S}.quizzes.id", f"{S}.quizzes.workspace_id"],
            ondelete="CASCADE",
        ),
        schema=S,
    )
    op.create_table(
        "quiz_attempts",
        pk(),
        public_id(),
        workspace(),
        sa.Column("quiz_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("weak_topics", JSONB()),
        sa.Column("fence", UUID()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(64)),
        created(),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "workspace_id", name="uq_quiz_attempts_scope"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_quiz_attempts_key"),
        sa.ForeignKeyConstraint(
            ["quiz_id", "workspace_id"],
            [f"{S}.quizzes.id", f"{S}.quizzes.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('in_progress','grading','submitted','failed')",
            name="ck_quiz_attempts_status",
        ),
        schema=S,
    )
    op.create_index(
        "ix_quiz_attempts_workspace",
        "quiz_attempts",
        ["workspace_id", "quiz_id", "created_at"],
        schema=S,
    )
    op.create_table(
        "quiz_answers",
        pk(),
        workspace(),
        sa.Column("attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("response", JSONB(), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("feedback", sa.String(500)),
        sa.Column("grading_method", sa.String(20)),
        sa.UniqueConstraint("attempt_id", "question_id", name="uq_quiz_answers_pair"),
        sa.ForeignKeyConstraint(
            ["attempt_id", "workspace_id"],
            [f"{S}.quiz_attempts.id", f"{S}.quiz_attempts.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_id", "workspace_id"],
            [f"{S}.quiz_questions.id", f"{S}.quiz_questions.workspace_id"],
            ondelete="CASCADE",
        ),
        schema=S,
    )
    # A deleted parse version must remove every learning record that could retain its text.
    for association, parent, key in (
        ("conversation_documents", "conversations", "conversation_id"),
        ("quiz_documents", "quizzes", "quiz_id"),
    ):
        op.execute(f"""
            CREATE FUNCTION {S}.purge_{association}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                DELETE FROM {S}.{parent} WHERE id = OLD.{key};
                RETURN OLD;
            END $$
        """)
        op.execute(f"""
            CREATE TRIGGER purge_{association}_on_version_delete
            AFTER DELETE ON {S}.{association}
            FOR EACH ROW EXECUTE FUNCTION {S}.purge_{association}()
        """)


def downgrade() -> None:
    # Destructive; production rollback keeps these tables and rolls forward instead.
    for association in ("quiz_documents", "conversation_documents"):
        op.execute(f"DROP TRIGGER purge_{association}_on_version_delete ON {S}.{association}")
        op.execute(f"DROP FUNCTION {S}.purge_{association}()")
    for table in (
        "quiz_answers",
        "quiz_attempts",
        "quiz_questions",
        "quiz_documents",
        "quizzes",
        "answer_feedback",
        "conversation_messages",
        "conversation_documents",
        "conversations",
        "collection_documents",
        "collections",
    ):
        op.drop_table(table, schema=S)
