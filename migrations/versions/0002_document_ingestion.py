"""Add private PDF ingestion records and persistent processing jobs.

Revision ID: 0002_document_ingestion
Revises: 0001_database_baseline
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.database_schema import APPLICATION_SCHEMA

revision: str = "0002_document_ingestion"
down_revision: str | None = "0001_database_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "public_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'deleted')", name="ck_workspaces_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id", name="uq_workspaces_public_id"),
        schema=APPLICATION_SCHEMA,
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "public_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("upload_idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="uploaded", nullable=False),
        sa.Column("active_version_id", sa.BigInteger(), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("byte_size > 0", name="ck_documents_byte_size_positive"),
        sa.CheckConstraint("page_count IS NULL OR page_count >= 0", name="ck_documents_page_count"),
        sa.CheckConstraint(
            "status IN ('uploaded', 'queued', 'parsing', 'ready', 'failed', 'deleting', 'deleted')",
            name="ck_documents_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{APPLICATION_SCHEMA}.workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key", name="uq_documents_object_key"),
        sa.UniqueConstraint("public_id", name="uq_documents_public_id"),
        sa.UniqueConstraint(
            "workspace_id", "upload_idempotency_key", name="uq_documents_upload_key"
        ),
        schema=APPLICATION_SCHEMA,
    )
    op.create_index(
        "ix_documents_workspace_id",
        "documents",
        ["workspace_id"],
        schema=APPLICATION_SCHEMA,
    )
    op.create_index(
        "ix_documents_workspace_status_created",
        "documents",
        ["workspace_id", "status", "created_at", "id"],
        schema=APPLICATION_SCHEMA,
    )
    op.create_index(
        "uq_documents_workspace_sha256_active",
        "documents",
        ["workspace_id", "sha256"],
        unique=True,
        schema=APPLICATION_SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "document_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_name", sa.String(length=80), nullable=False),
        sa.Column("parser_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="ready", nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version_no > 0", name="ck_document_versions_number_positive"),
        sa.CheckConstraint("page_count >= 0", name="ck_document_versions_page_count"),
        sa.CheckConstraint("status IN ('ready')", name="ck_document_versions_status"),
        sa.ForeignKeyConstraint(
            ["document_id"], [f"{APPLICATION_SCHEMA}.documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "source_sha256",
            "parser_name",
            "parser_version",
            name="uq_document_versions_parser_source",
        ),
        sa.UniqueConstraint("document_id", "version_no", name="uq_document_versions_number"),
        schema=APPLICATION_SCHEMA,
    )
    op.create_index(
        "ix_document_versions_document_id",
        "document_versions",
        ["document_id"],
        schema=APPLICATION_SCHEMA,
    )
    op.create_foreign_key(
        "fk_documents_active_version_id",
        "documents",
        "document_versions",
        ["active_version_id"],
        ["id"],
        source_schema=APPLICATION_SCHEMA,
        referent_schema=APPLICATION_SCHEMA,
        ondelete="SET NULL",
    )
    op.create_table(
        "document_pages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("document_version_id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("page_number > 0", name="ck_document_pages_number_positive"),
        sa.CheckConstraint("char_count >= 0", name="ck_document_pages_char_count"),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            [f"{APPLICATION_SCHEMA}.document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{APPLICATION_SCHEMA}.workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_version_id", "page_number", name="uq_document_pages_number"),
        schema=APPLICATION_SCHEMA,
    )
    op.create_index(
        "ix_document_pages_workspace_version",
        "document_pages",
        ["workspace_id", "document_version_id"],
        schema=APPLICATION_SCHEMA,
    )
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "public_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("job_type", sa.String(length=30), server_default="pdf_parse", nullable=False),
        sa.Column("idempotency_key", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("retry_of_job_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("job_type IN ('pdf_parse')", name="ck_processing_jobs_type"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'cancelled')",
            name="ck_processing_jobs_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_processing_jobs_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="ck_processing_jobs_max_attempts"),
        sa.ForeignKeyConstraint(
            ["document_id"], [f"{APPLICATION_SCHEMA}.documents.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_job_id"],
            [f"{APPLICATION_SCHEMA}.processing_jobs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{APPLICATION_SCHEMA}.workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id", name="uq_processing_jobs_public_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "job_type",
            "idempotency_key",
            name="uq_processing_jobs_idempotency",
        ),
        schema=APPLICATION_SCHEMA,
    )
    op.create_index(
        "ix_processing_jobs_available",
        "processing_jobs",
        ["available_at", "created_at"],
        schema=APPLICATION_SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'processing')"),
    )
    for index_name, column_name in (
        ("ix_processing_jobs_workspace_id", "workspace_id"),
        ("ix_processing_jobs_document_id", "document_id"),
        ("ix_processing_jobs_retry_of_job_id", "retry_of_job_id"),
    ):
        op.create_index(
            index_name,
            "processing_jobs",
            [column_name],
            schema=APPLICATION_SCHEMA,
        )


def downgrade() -> None:
    op.drop_table("processing_jobs", schema=APPLICATION_SCHEMA)
    op.drop_table("document_pages", schema=APPLICATION_SCHEMA)
    op.drop_constraint(
        "fk_documents_active_version_id",
        "documents",
        schema=APPLICATION_SCHEMA,
        type_="foreignkey",
    )
    op.drop_table("document_versions", schema=APPLICATION_SCHEMA)
    op.drop_table("documents", schema=APPLICATION_SCHEMA)
    op.drop_table("workspaces", schema=APPLICATION_SCHEMA)
