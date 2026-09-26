from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database_schema import APPLICATION_SCHEMA as S
from app.core.models import Base


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_collections_scope"),
        ForeignKeyConstraint(["workspace_id"], [f"{S}.workspaces.id"], ondelete="CASCADE"),
        Index("ix_collections_workspace", "workspace_id", "created_at"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CollectionDocument(Base):
    __tablename__ = "collection_documents"
    __table_args__ = (
        UniqueConstraint("collection_id", "document_id", name="uq_collection_documents_pair"),
        ForeignKeyConstraint(
            ["collection_id", "workspace_id"],
            [f"{S}.collections.id", f"{S}.collections.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "workspace_id"],
            [f"{S}.documents.id", f"{S}.documents.workspace_id"],
            ondelete="CASCADE",
        ),
        Index("ix_collection_documents_document", "workspace_id", "document_id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    collection_id: Mapped[int] = mapped_column(BigInteger)
    document_id: Mapped[int] = mapped_column(BigInteger)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_conversations_scope"),
        ForeignKeyConstraint(["workspace_id"], [f"{S}.workspaces.id"], ondelete="CASCADE"),
        Index("ix_conversations_workspace", "workspace_id", "created_at"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(160))
    scope: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConversationDocument(Base):
    __tablename__ = "conversation_documents"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "document_version_id", name="uq_conversation_documents_pair"
        ),
        ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{S}.conversations.id", f"{S}.conversations.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            [f"{S}.document_versions.id", f"{S}.document_versions.workspace_id"],
            ondelete="CASCADE",
        ),
        Index("ix_conversation_documents_version", "workspace_id", "document_version_id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    conversation_id: Mapped[int] = mapped_column(BigInteger)
    document_version_id: Mapped[int] = mapped_column(BigInteger)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_conversation_messages_key"),
        UniqueConstraint("id", "workspace_id", name="uq_conversation_messages_scope"),
        ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{S}.conversations.id", f"{S}.conversations.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["quiz_id", "workspace_id"],
            [f"{S}.quizzes.id", f"{S}.quizzes.workspace_id"],
            name="fk_conversation_message_quiz_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('queued','processing','answered','insufficient','failed','cancelled')",
            name="ck_conversation_messages_status",
        ),
        Index("ix_conversation_messages_claim", "status", "created_at"),
        Index("ix_conversation_messages_history", "workspace_id", "conversation_id", "created_at"),
        Index("ix_conversation_messages_quiz", "workspace_id", "quiz_id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    conversation_id: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    question: Mapped[str] = mapped_column(String(2000))
    scope: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    profile: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    task_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    quiz_id: Mapped[int | None] = mapped_column(BigInteger)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fence: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnswerFeedback(Base):
    __tablename__ = "answer_feedback"
    __table_args__ = (
        UniqueConstraint("workspace_id", "message_id", name="uq_answer_feedback_message"),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_answer_feedback_key"),
        ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            [f"{S}.conversation_messages.id", f"{S}.conversation_messages.workspace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "rating IN ('helpful','unhelpful','citation_inaccurate')",
            name="ck_answer_feedback_rating",
        ),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    rating: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Quiz(Base):
    __tablename__ = "quizzes"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_quizzes_scope"),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_quizzes_key"),
        ForeignKeyConstraint(["workspace_id"], [f"{S}.workspaces.id"], ondelete="CASCADE"),
        CheckConstraint(
            "status IN ('queued','processing','ready','failed')", name="ck_quizzes_status"
        ),
        Index("ix_quizzes_claim", "status", "created_at"),
        Index("ix_quizzes_workspace", "workspace_id", "created_at"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(160))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    requested_scope: Mapped[dict[str, list[str]]] = mapped_column(JSONB)
    scope: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    profile: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    generation_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fence: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuizDocument(Base):
    __tablename__ = "quiz_documents"
    __table_args__ = (
        UniqueConstraint("quiz_id", "document_version_id", name="uq_quiz_documents_pair"),
        ForeignKeyConstraint(
            ["quiz_id", "workspace_id"],
            [f"{S}.quizzes.id", f"{S}.quizzes.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            [f"{S}.document_versions.id", f"{S}.document_versions.workspace_id"],
            ondelete="CASCADE",
        ),
        Index("ix_quiz_documents_version", "workspace_id", "document_version_id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    quiz_id: Mapped[int] = mapped_column(BigInteger)
    document_version_id: Mapped[int] = mapped_column(BigInteger)


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    __table_args__ = (
        UniqueConstraint("quiz_id", "ordinal", name="uq_quiz_questions_ordinal"),
        UniqueConstraint("id", "workspace_id", name="uq_quiz_questions_scope"),
        ForeignKeyConstraint(
            ["quiz_id", "workspace_id"],
            [f"{S}.quizzes.id", f"{S}.quizzes.workspace_id"],
            ondelete="CASCADE",
        ),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    quiz_id: Mapped[int] = mapped_column(BigInteger)
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    difficulty: Mapped[str] = mapped_column(String(12))
    topic: Mapped[str] = mapped_column(String(120))
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str]] = mapped_column(JSONB)
    answer: Mapped[Any] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    schema_version: Mapped[str] = mapped_column(String(40))


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_quiz_attempts_scope"),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_quiz_attempts_key"),
        ForeignKeyConstraint(
            ["quiz_id", "workspace_id"],
            [f"{S}.quizzes.id", f"{S}.quizzes.workspace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('in_progress','grading','submitted','failed')",
            name="ck_quiz_attempts_status",
        ),
        Index("ix_quiz_attempts_workspace", "workspace_id", "quiz_id", "created_at"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    quiz_id: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    score: Mapped[float | None]
    weak_topics: Mapped[list[str] | None] = mapped_column(JSONB)
    fence: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizAnswer(Base):
    __tablename__ = "quiz_answers"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_id", name="uq_quiz_answers_pair"),
        ForeignKeyConstraint(
            ["attempt_id", "workspace_id"],
            [f"{S}.quiz_attempts.id", f"{S}.quiz_attempts.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["question_id", "workspace_id"],
            [f"{S}.quiz_questions.id", f"{S}.quiz_questions.workspace_id"],
            ondelete="CASCADE",
        ),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    attempt_id: Mapped[int] = mapped_column(BigInteger)
    question_id: Mapped[int] = mapped_column(BigInteger)
    response: Mapped[Any] = mapped_column(JSONB)
    score: Mapped[float | None]
    feedback: Mapped[str | None] = mapped_column(String(500))
    grading_method: Mapped[str | None] = mapped_column(String(20))
