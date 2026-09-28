"""Workspace-owned workflow ledger and private, short-lived call checkpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database_schema import APPLICATION_SCHEMA as S
from app.core.models import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_agent_runs_scope"),
        UniqueConstraint("message_id", "workspace_id", name="uq_agent_runs_message"),
        UniqueConstraint("attempt_id", "workspace_id", name="uq_agent_runs_attempt"),
        ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            [f"{S}.conversation_messages.id", f"{S}.conversation_messages.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{S}.conversations.id", f"{S}.conversations.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["quiz_id", "workspace_id"],
            [f"{S}.quizzes.id", f"{S}.quizzes.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "quiz_id", "workspace_id"],
            [
                f"{S}.quiz_attempts.id",
                f"{S}.quiz_attempts.quiz_id",
                f"{S}.quiz_attempts.workspace_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('queued','running','waiting_input','waiting_result',"
            "'blocked','completed','failed','cancelled','expired')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint("revision >= 0", name="ck_agent_runs_revision"),
        Index("ix_agent_runs_claim", "status", "created_at"),
        Index("ix_agent_runs_conversation", "workspace_id", "conversation_id", "id"),
        Index("ix_agent_runs_attempt", "workspace_id", "attempt_id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    conversation_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    graph_version: Mapped[str] = mapped_column(String(40))
    profile: Mapped[str] = mapped_column(String(160))
    scope: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    stage: Mapped[str] = mapped_column(String(20), default="plan")
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    outputs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    quiz_id: Mapped[int | None] = mapped_column(BigInteger)
    attempt_id: Mapped[int | None] = mapped_column(BigInteger)
    quiz_reserved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    clarification: Mapped[str | None] = mapped_column(String(600))
    response: Mapped[str | None] = mapped_column(String(1200))
    response_key: Mapped[str | None] = mapped_column(String(200))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    fence: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentStageExecution(Base):
    __tablename__ = "agent_stage_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            [f"{S}.agent_runs.id", f"{S}.agent_runs.workspace_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", "stage", "ordinal", name="uq_agent_stage_call"),
        CheckConstraint(
            "status IN ('calling','returned','published')", name="ck_agent_stage_status"
        ),
        CheckConstraint("ordinal >= 0 AND ordinal < 12", name="ck_agent_stage_ordinal"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    run_id: Mapped[int] = mapped_column(BigInteger)
    stage: Mapped[str] = mapped_column(String(20))
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
