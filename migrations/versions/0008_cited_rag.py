"""Add resumable document indexes and version-scoped cited questions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0008_cited_rag"
down_revision: str | None = "0007_unified_citation_locator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_indexes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("public_id", UUID(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("document_version_id", sa.BigInteger(), nullable=False),
        sa.Column("profile", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("prepared", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("retry_key", sa.String(200), nullable=False),
        sa.Column("fence", UUID(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("failures", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("document_version_id", "profile", name="uq_document_indexes_profile"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_document_indexes_workspace"),
        sa.ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            [f"{S}.document_versions.id", f"{S}.document_versions.workspace_id"],
            ondelete="CASCADE",
            name="fk_document_indexes_version",
        ),
        sa.CheckConstraint(
            "status IN ('queued','processing','ready','failed')", name="ck_document_indexes_status"
        ),
        schema=S,
    )
    op.create_index(
        "ix_document_indexes_claim", "document_indexes", ["status", "created_at"], schema=S
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("public_id", UUID(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("index_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("unit", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.UniqueConstraint("index_id", "ordinal", name="uq_document_chunks_ordinal"),
        sa.ForeignKeyConstraint(
            ["index_id", "workspace_id"],
            [f"{S}.document_indexes.id", f"{S}.document_indexes.workspace_id"],
            ondelete="CASCADE",
            name="fk_document_chunks_index",
        ),
        sa.CheckConstraint(
            "ordinal > 0 AND unit > 0 AND start_offset >= 0 AND end_offset > start_offset",
            name="ck_document_chunks_offsets",
        ),
        schema=S,
    )
    op.create_index(
        "ix_document_chunks_scope", "document_chunks", ["workspace_id", "index_id"], schema=S
    )
    op.create_table(
        "rag_questions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("public_id", UUID(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("document_version_id", sa.BigInteger(), nullable=False),
        sa.Column("profile", sa.String(160), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("question", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("answer", JSONB(), nullable=True),
        sa.Column("usage", JSONB(), nullable=True),
        sa.Column("fence", UUID(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_rag_questions_key"),
        sa.ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            [f"{S}.document_versions.id", f"{S}.document_versions.workspace_id"],
            ondelete="CASCADE",
            name="fk_rag_questions_version",
        ),
        sa.CheckConstraint(
            "status IN ('queued','processing','answered','insufficient','failed','cancelled')",
            name="ck_rag_questions_status",
        ),
        schema=S,
    )
    op.create_index("ix_rag_questions_claim", "rag_questions", ["status", "created_at"], schema=S)
    op.create_index(
        "ix_rag_questions_scope",
        "rag_questions",
        ["workspace_id", "document_version_id", "created_at"],
        schema=S,
    )


def downgrade() -> None:
    # Explicitly destructive: only use on disposable databases; production rolls forward.
    op.drop_table("rag_questions", schema=S)
    op.drop_table("document_chunks", schema=S)
    op.drop_table("document_indexes", schema=S)
