"""Scoped SQL persistence for personal spaced review and rating receipts."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.auth.models import User, WorkspaceMember
from app.core.errors import ApplicationError
from app.documents.infrastructure.models import DocumentModel, DocumentVersionModel, WorkspaceModel
from app.learning.models import Quiz, QuizAnswer, QuizAttempt, QuizDocument, QuizQuestion
from app.learning.review import REVIEW_POLICY_VERSION, ReviewRating, schedule_review
from app.learning.review_models import ReviewCard, ReviewEvent
from app.learning.scope import missing
from app.learning.store import question_view


def conflict(code: str, message: str) -> ApplicationError:
    return ApplicationError(code=code, message=message, status_code=409)


def review_sources_available() -> ColumnElement[bool]:
    # Hide content as soon as deletion starts, before the version-purge job completes.
    unavailable = (
        select(QuizDocument.id)
        .join(
            DocumentVersionModel,
            and_(
                DocumentVersionModel.id == QuizDocument.document_version_id,
                DocumentVersionModel.workspace_id == QuizDocument.workspace_id,
            ),
        )
        .join(
            DocumentModel,
            and_(
                DocumentModel.id == DocumentVersionModel.document_id,
                DocumentModel.workspace_id == QuizDocument.workspace_id,
            ),
        )
        .where(
            QuizDocument.quiz_id == Quiz.id,
            QuizDocument.workspace_id == Quiz.workspace_id,
            or_(
                DocumentModel.status.in_(["deleting", "deleted"]),
                DocumentModel.deleted_at.is_not(None),
            ),
        )
        .correlate(Quiz)
        .exists()
    )
    return ~unavailable


class SqlReviewStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def _member(
        self, session: AsyncSession, workspace: UUID, user: UUID, *, lock: bool = False
    ) -> tuple[int, int]:
        query = (
            select(WorkspaceMember.workspace_id, WorkspaceMember.user_id)
            .join(User, User.id == WorkspaceMember.user_id)
            .join(WorkspaceModel, WorkspaceModel.id == WorkspaceMember.workspace_id)
            .where(
                WorkspaceModel.public_id == workspace,
                WorkspaceModel.status == "active",
                User.public_id == user,
                User.status == "active",
            )
        )
        if lock:
            # Serialize this member's mutations, including retries with the same key.
            query = query.with_for_update(of=WorkspaceMember)
        row = (await session.execute(query)).one_or_none()
        if row is None:
            raise missing()
        return row[0], row[1]

    async def _card(
        self, session: AsyncSession, owner: int, member: int, card: UUID
    ) -> tuple[ReviewCard, QuizQuestion]:
        row = (
            await session.execute(
                select(ReviewCard, QuizQuestion)
                .join(
                    QuizQuestion,
                    and_(
                        QuizQuestion.id == ReviewCard.question_id,
                        QuizQuestion.workspace_id == ReviewCard.workspace_id,
                    ),
                )
                .join(Quiz, and_(Quiz.id == QuizQuestion.quiz_id, Quiz.workspace_id == owner))
                .where(
                    ReviewCard.workspace_id == owner,
                    ReviewCard.user_id == member,
                    ReviewCard.public_id == card,
                    Quiz.status == "ready",
                    review_sources_available(),
                )
            )
        ).one_or_none()
        if row is None:
            raise missing()
        return row[0], row[1]

    async def queue(self, workspace: UUID, user: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            owner, member = await self._member(session, workspace, user)
            now = datetime.now(UTC)
            query = (
                select(ReviewCard, QuizQuestion)
                .join(
                    QuizQuestion,
                    and_(
                        QuizQuestion.id == ReviewCard.question_id,
                        QuizQuestion.workspace_id == owner,
                    ),
                )
                .join(Quiz, and_(Quiz.id == QuizQuestion.quiz_id, Quiz.workspace_id == owner))
                .where(
                    ReviewCard.workspace_id == owner,
                    ReviewCard.user_id == member,
                    Quiz.status == "ready",
                    review_sources_available(),
                )
            )
            counts = (
                await session.execute(
                    select(
                        func.count().filter(ReviewCard.due_at <= now),
                        func.count().filter(ReviewCard.due_at > now),
                    )
                    .select_from(ReviewCard)
                    .join(
                        QuizQuestion,
                        and_(
                            QuizQuestion.id == ReviewCard.question_id,
                            QuizQuestion.workspace_id == owner,
                        ),
                    )
                    .join(Quiz, and_(Quiz.id == QuizQuestion.quiz_id, Quiz.workspace_id == owner))
                    .where(
                        ReviewCard.workspace_id == owner,
                        ReviewCard.user_id == member,
                        Quiz.status == "ready",
                        review_sources_available(),
                    )
                )
            ).one()
            due = (
                await session.execute(
                    query.where(ReviewCard.due_at <= now)
                    .order_by(ReviewCard.due_at, ReviewCard.id)
                    .limit(20)
                )
            ).all()
            upcoming = (
                await session.execute(
                    query.where(ReviewCard.due_at > now)
                    .order_by(ReviewCard.due_at, ReviewCard.id)
                    .limit(20)
                )
            ).all()
            return {
                "due_count": counts[0],
                "upcoming_count": counts[1],
                "items": [
                    {
                        "id": str(card.public_id),
                        "due_at": card.due_at,
                        "revision": card.revision,
                        "review_count": card.revision,
                        "question": question_view(question, show_answer=False),
                    }
                    for card, question in due
                ],
                "upcoming": [
                    {
                        "id": str(card.public_id),
                        "due_at": card.due_at,
                        "topic": question.topic,
                        "interval_days": card.interval_days,
                    }
                    for card, question in upcoming
                ],
            }

    async def enroll(
        self, workspace: UUID, user: UUID, quiz: UUID, attempt: UUID
    ) -> dict[str, int]:
        async with self.sessions.begin() as session:
            owner, member = await self._member(session, workspace, user, lock=True)
            row = await session.scalar(
                select(QuizAttempt)
                .join(Quiz, and_(Quiz.id == QuizAttempt.quiz_id, Quiz.workspace_id == owner))
                .where(
                    QuizAttempt.workspace_id == owner,
                    QuizAttempt.public_id == attempt,
                    Quiz.public_id == quiz,
                    Quiz.status == "ready",
                    review_sources_available(),
                )
            )
            if row is None:
                raise missing()
            if row.status != "submitted":
                raise conflict("ATTEMPT_NOT_SUBMITTED", "请先完成作答并等待评分")
            questions = list(
                await session.scalars(
                    select(QuizQuestion.id)
                    .outerjoin(
                        QuizAnswer,
                        and_(
                            QuizAnswer.question_id == QuizQuestion.id,
                            QuizAnswer.workspace_id == owner,
                            QuizAnswer.attempt_id == row.id,
                        ),
                    )
                    .where(
                        QuizQuestion.workspace_id == owner,
                        QuizQuestion.quiz_id == row.quiz_id,
                        func.coalesce(QuizAnswer.score, 0) < 0.7,
                    )
                )
            )
            if not questions:
                return {"added": 0, "existing": 0}
            added = list(
                await session.scalars(
                    insert(ReviewCard)
                    .values(
                        [
                            {"workspace_id": owner, "user_id": member, "question_id": question}
                            for question in questions
                        ]
                    )
                    .on_conflict_do_nothing(constraint="uq_review_card_question")
                    .returning(ReviewCard.id)
                )
            )
            return {"added": len(added), "existing": len(questions) - len(added)}

    async def answer(self, workspace: UUID, user: UUID, card: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            owner, member = await self._member(session, workspace, user)
            _, question = await self._card(session, owner, member, card)
            return question_view(question, show_answer=True)

    async def rate(
        self,
        workspace: UUID,
        user: UUID,
        card: UUID,
        rating: ReviewRating,
        revision: int,
        key: str,
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            owner, member = await self._member(session, workspace, user, lock=True)
            row, _ = await self._card(session, owner, member, card)
            receipt = await session.scalar(
                select(ReviewEvent).where(
                    ReviewEvent.workspace_id == owner,
                    ReviewEvent.user_id == member,
                    ReviewEvent.idempotency_key == key,
                )
            )
            if receipt is not None:
                if (receipt.card_id, receipt.rating, receipt.expected_revision) != (
                    row.id,
                    rating,
                    revision,
                ):
                    raise conflict("IDEMPOTENCY_CONFLICT", "该请求已用于其他复习记录")
                return receipt.result
            if row.revision != revision:
                raise conflict("REVIEW_REVISION_CONFLICT", "此题已在其他页面复习，请刷新队列")
            now = datetime.now(UTC)
            if row.due_at > now:
                raise conflict("REVIEW_NOT_DUE", "此题尚未到复习时间")
            schedule = schedule_review(
                rating, stage=row.stage, interval_days=row.interval_days, lapses=row.lapses, now=now
            )
            row.stage, row.interval_days = schedule.stage, schedule.interval_days
            row.due_at, row.lapses = schedule.due_at, schedule.lapses
            row.revision += 1
            row.policy_version = REVIEW_POLICY_VERSION
            result = {
                "id": str(row.public_id),
                "due_at": row.due_at.isoformat(),
                "interval_days": row.interval_days,
                "revision": row.revision,
            }
            session.add(
                ReviewEvent(
                    card_id=row.id,
                    workspace_id=owner,
                    user_id=member,
                    idempotency_key=key,
                    rating=rating,
                    expected_revision=revision,
                    result=result,
                )
            )
            return result
