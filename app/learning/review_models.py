"""Personal review state; content remains in validated Quiz questions."""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database_schema import APPLICATION_SCHEMA as S
from app.core.models import Base


class ReviewCard(Base):
    __tablename__ = "review_cards"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "question_id", name="uq_review_card_question"),
        UniqueConstraint("id", "workspace_id", "user_id", name="uq_review_card_owner"),
        ForeignKeyConstraint(
            ["question_id", "workspace_id"],
            [f"{S}.quiz_questions.id", f"{S}.quiz_questions.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["user_id", "workspace_id"],
            [f"{S}.workspace_members.user_id", f"{S}.workspace_members.workspace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "stage BETWEEN 0 AND 6 AND interval_days BETWEEN 0 AND 120 "
            "AND revision >= 0 AND lapses >= 0",
            name="ck_review_card_schedule",
        ),
        Index("ix_review_card_question", "question_id", "workspace_id"),
        Index("ix_review_card_due", "workspace_id", "user_id", "due_at", "id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), unique=True, default=uuid4
    )
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    question_id: Mapped[int] = mapped_column(BigInteger)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stage: Mapped[int] = mapped_column(Integer, server_default="0")
    interval_days: Mapped[int] = mapped_column(Integer, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, server_default="0")
    lapses: Mapped[int] = mapped_column(Integer, server_default="0")
    policy_version: Mapped[str] = mapped_column(String(40), server_default="spaced-review-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewEvent(Base):
    __tablename__ = "review_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "idempotency_key", name="uq_review_event_key"),
        ForeignKeyConstraint(
            ["card_id", "workspace_id", "user_id"],
            [f"{S}.review_cards.id", f"{S}.review_cards.workspace_id", f"{S}.review_cards.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "rating IN ('again','hard','good') AND expected_revision >= 0",
            name="ck_review_event_rating",
        ),
        Index("ix_review_event_card", "card_id", "workspace_id", "user_id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    card_id: Mapped[int] = mapped_column(BigInteger)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    rating: Mapped[str] = mapped_column(String(10))
    expected_revision: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
