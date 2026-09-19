from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database_schema import APPLICATION_SCHEMA
from app.core.models import Base


class WorkspaceModel(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'deleted')", name="ck_workspaces_status"
        ),
        {"schema": APPLICATION_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), unique=True, nullable=False, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "upload_idempotency_key", name="uq_documents_upload_key"),
        CheckConstraint("byte_size > 0", name="ck_documents_byte_size_positive"),
        CheckConstraint("page_count IS NULL OR page_count >= 0", name="ck_documents_page_count"),
        CheckConstraint(
            "status IN ('uploaded', 'queued', 'parsing', 'ready', 'failed', 'deleting', 'deleted')",
            name="ck_documents_status",
        ),
        Index(
            "uq_documents_workspace_sha256_active",
            "workspace_id",
            "sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_documents_workspace_status_created", "workspace_id", "status", "created_at", "id"
        ),
        Index("ix_documents_workspace_created", "workspace_id", "created_at", "id"),
        Index("ix_documents_workspace_id", "workspace_id"),
        {"schema": APPLICATION_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), unique=True, nullable=False, default=uuid4
    )
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APPLICATION_SCHEMA}.workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    upload_idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    active_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            f"{APPLICATION_SCHEMA}.document_versions.id",
            name="fk_documents_active_version_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentVersionModel(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no", name="uq_document_versions_number"),
        UniqueConstraint(
            "document_id",
            "source_sha256",
            "parser_name",
            "parser_version",
            name="uq_document_versions_parser_source",
        ),
        CheckConstraint("version_no > 0", name="ck_document_versions_number_positive"),
        CheckConstraint("page_count >= 0", name="ck_document_versions_page_count"),
        CheckConstraint("status IN ('ready')", name="ck_document_versions_status"),
        Index("ix_document_versions_document_id", "document_id"),
        {"schema": APPLICATION_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APPLICATION_SCHEMA}.documents.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_name: Mapped[str] = mapped_column(String(80), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentPageModel(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_version_id", "page_number", name="uq_document_pages_number"),
        CheckConstraint("page_number > 0", name="ck_document_pages_number_positive"),
        CheckConstraint("char_count >= 0", name="ck_document_pages_char_count"),
        Index("ix_document_pages_workspace_version", "workspace_id", "document_version_id"),
        {"schema": APPLICATION_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APPLICATION_SCHEMA}.document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APPLICATION_SCHEMA}.workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)


class ProcessingJobModel(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "job_type", "idempotency_key", name="uq_processing_jobs_idempotency"
        ),
        CheckConstraint("job_type IN ('pdf_parse')", name="ck_processing_jobs_type"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'cancelled')",
            name="ck_processing_jobs_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_processing_jobs_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_processing_jobs_max_attempts"),
        Index(
            "ix_processing_jobs_available",
            "available_at",
            "created_at",
            postgresql_where=text("status IN ('queued', 'processing')"),
        ),
        Index("ix_processing_jobs_workspace_id", "workspace_id"),
        Index("ix_processing_jobs_document_id", "document_id"),
        Index("ix_processing_jobs_retry_of_job_id", "retry_of_job_id"),
        {"schema": APPLICATION_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), unique=True, nullable=False, default=uuid4
    )
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APPLICATION_SCHEMA}.workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey(f"{APPLICATION_SCHEMA}.documents.id", ondelete="RESTRICT"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(String(30), nullable=False, default="pdf_parse")
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(String(500))
    retry_of_job_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{APPLICATION_SCHEMA}.processing_jobs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
