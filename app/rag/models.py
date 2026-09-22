from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database_schema import APPLICATION_SCHEMA as S
from app.core.models import Base


class DocumentIndex(Base):
    __tablename__ = "document_indexes"
    __table_args__ = (
        UniqueConstraint("document_version_id", "profile", name="uq_document_indexes_profile"),
        UniqueConstraint("id", "workspace_id", name="uq_document_indexes_workspace"),
        ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            [f"{S}.document_versions.id", f"{S}.document_versions.workspace_id"],
            ondelete="CASCADE",
            name="fk_document_indexes_version",
        ),
        CheckConstraint(
            "status IN ('queued','processing','ready','failed')", name="ck_document_indexes_status"
        ),
        Index("ix_document_indexes_claim", "status", "created_at"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    document_version_id: Mapped[int] = mapped_column(BigInteger)
    profile: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    prepared: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    retry_key: Mapped[str] = mapped_column(String(200))
    fence: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failures: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("index_id", "ordinal", name="uq_document_chunks_ordinal"),
        ForeignKeyConstraint(
            ["index_id", "workspace_id"],
            [f"{S}.document_indexes.id", f"{S}.document_indexes.workspace_id"],
            ondelete="CASCADE",
            name="fk_document_chunks_index",
        ),
        CheckConstraint(
            "ordinal > 0 AND unit > 0 AND start_offset >= 0 AND end_offset > start_offset",
            name="ck_document_chunks_offsets",
        ),
        Index("ix_document_chunks_scope", "workspace_id", "index_id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    index_id: Mapped[int] = mapped_column(BigInteger)
    ordinal: Mapped[int] = mapped_column(Integer)
    unit: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))


class RagQuestion(Base):
    __tablename__ = "rag_questions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_rag_questions_key"),
        ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            [f"{S}.document_versions.id", f"{S}.document_versions.workspace_id"],
            ondelete="CASCADE",
            name="fk_rag_questions_version",
        ),
        CheckConstraint(
            "status IN ('queued','processing','answered','insufficient','failed','cancelled')",
            name="ck_rag_questions_status",
        ),
        Index("ix_rag_questions_claim", "status", "created_at"),
        Index("ix_rag_questions_scope", "workspace_id", "document_version_id", "created_at"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    document_version_id: Mapped[int] = mapped_column(BigInteger)
    profile: Mapped[str] = mapped_column(String(160))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    question: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fence: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
