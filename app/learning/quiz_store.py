"""Quiz creation, shared hourly quota, generation claims and publication."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApplicationError
from app.documents.infrastructure.models import WorkspaceModel
from app.learning.models import (
    ConversationMessage,
    Quiz,
    QuizDocument,
    QuizQuestion,
)
from app.learning.run_models import AgentRun
from app.learning.scope import (
    LEASE_SECONDS,
    ScopeSnapshot,
    missing,
    quiz_evidence,
    resolve_scope,
    snapshot_ready,
    workspace_id,
)
from app.rag.domain import Evidence, RagFailure
from app.rag.messages import FAILURES


def quiz_view(quiz: Quiz, question_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(quiz.public_id),
        "title": quiz.title,
        "status": quiz.status,
        "config": quiz.config,
        "scope": quiz.scope,
        "question_count": question_count,
        "failure_code": quiz.failure_code,
        "failure_message": FAILURES.get(quiz.failure_code or ""),
        "created_at": quiz.created_at,
    }


def question_view(question: QuizQuestion, *, show_answer: bool) -> dict[str, Any]:
    return {
        "id": str(question.public_id),
        "ordinal": question.ordinal,
        "kind": question.kind,
        "difficulty": question.difficulty,
        "topic": question.topic,
        "stem": question.stem,
        "options": question.options,
        "sources": question.sources if show_answer else [],
        "answer": question.answer if show_answer else None,
        "explanation": question.explanation if show_answer else None,
    }


async def check_quiz_capacity(session: AsyncSession, workspace: int) -> None:
    await session.scalar(
        select(WorkspaceModel.id).where(WorkspaceModel.id == workspace).with_for_update()
    )
    since = datetime.now(UTC) - timedelta(hours=1)
    quizzes = await session.scalar(
        select(func.count())
        .select_from(Quiz)
        .where(
            Quiz.workspace_id == workspace,
            Quiz.created_at > since,
        )
    )
    reserved = await session.scalar(
        select(func.count())
        .select_from(ConversationMessage)
        .where(
            ConversationMessage.workspace_id == workspace,
            ConversationMessage.status.in_(["queued", "processing"]),
            ConversationMessage.task_result["kind"].astext == "quiz",
        )
    )
    graph_reserved = await session.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.workspace_id == workspace, AgentRun.quiz_reserved.is_(True))
    )
    if (quizzes or 0) + (reserved or 0) + (graph_reserved or 0) >= 20:
        raise ApplicationError(
            code="QUIZ_LIMIT", message="出题过于频繁，请稍后重试", status_code=429
        )


async def load_quiz(
    session: AsyncSession, workspace: int, public_id: UUID, *, lock: bool = False
) -> Quiz:
    query = select(Quiz).where(Quiz.workspace_id == workspace, Quiz.public_id == public_id)
    if lock:
        query = query.with_for_update()
    item = await session.scalar(query)
    if item is None:
        raise missing()
    return item


async def question_count(session: AsyncSession, quiz_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id)
        )
        or 0
    )


class SqlQuizStore:
    """Quiz persistence; commands end their transactions before any model call."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], profile: str) -> None:
        self.sessions = sessions
        self.profile = profile

    async def create_quiz(
        self,
        principal: UUID,
        key: str,
        title: str,
        config: dict[str, Any],
        documents: list[UUID],
        collections: list[UUID],
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await workspace_id(session, principal)
            # Serialize replay lookup and creation, including requests using the same key.
            await session.scalar(
                select(WorkspaceModel.id).where(WorkspaceModel.id == workspace).with_for_update()
            )
            requested_scope = {
                "document_ids": sorted({str(item) for item in documents}),
                "collection_ids": sorted({str(item) for item in collections}),
            }
            existing = await session.scalar(
                select(Quiz).where(Quiz.workspace_id == workspace, Quiz.idempotency_key == key)
            )
            if existing is not None:
                if (
                    existing.config != config
                    or existing.title != title
                    or existing.requested_scope != requested_scope
                ):
                    raise ApplicationError(
                        code="IDEMPOTENCY_CONFLICT", message="请求标识已被使用", status_code=409
                    )
                return quiz_view(existing, await question_count(session, existing.id))
            await check_quiz_capacity(session, workspace)
            scope = await resolve_scope(session, workspace, documents, collections, self.profile)
            item = Quiz(
                workspace_id=workspace,
                idempotency_key=key,
                title=title,
                config=config,
                requested_scope=requested_scope,
                scope=scope,
                profile=self.profile,
                status="queued",
            )
            session.add(item)
            await session.flush()
            session.add_all(
                QuizDocument(
                    workspace_id=workspace, quiz_id=item.id, document_version_id=entry["version_id"]
                )
                for entry in scope
            )
            return quiz_view(item)

    async def list_quizzes(self, principal: UUID) -> list[dict[str, Any]]:
        async with self.sessions.begin() as session:
            workspace = await workspace_id(session, principal)
            items = (
                await session.scalars(
                    select(Quiz)
                    .where(Quiz.workspace_id == workspace)
                    .order_by(Quiz.created_at.desc(), Quiz.id.desc())
                    .limit(50)
                )
            ).all()
            return [quiz_view(item, await question_count(session, item.id)) for item in items]

    async def get_quiz(self, principal: UUID, quiz_id: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await workspace_id(session, principal)
            item = await load_quiz(session, workspace, quiz_id)
            questions = (
                await session.scalars(
                    select(QuizQuestion)
                    .where(QuizQuestion.workspace_id == workspace, QuizQuestion.quiz_id == item.id)
                    .order_by(QuizQuestion.ordinal)
                )
            ).all()
            return {
                **quiz_view(item, len(questions)),
                "questions": [question_view(question, show_answer=False) for question in questions],
            }

    async def claim_quiz(self) -> tuple[UUID, UUID, int, dict[str, Any], ScopeSnapshot] | None:
        async with self.sessions.begin() as session:
            now = datetime.now(UTC)
            await session.execute(
                update(Quiz)
                .where(Quiz.status == "processing", Quiz.lease_until < now)
                .values(status="failed", fence=None, failure_code="WORKER_INTERRUPTED")
            )
            quiz = await session.scalar(
                select(Quiz)
                .where(Quiz.status == "queued", Quiz.profile == self.profile)
                .order_by(Quiz.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if quiz is None:
                return None
            if not await snapshot_ready(session, quiz.workspace_id, quiz.scope, quiz.profile):
                quiz.status = "failed"
                quiz.failure_code = "SCOPE_NOT_READY"
                return None
            quiz.status = "processing"
            quiz.fence = uuid4()
            quiz.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            return quiz.public_id, quiz.fence, quiz.workspace_id, quiz.config, quiz.scope

    async def _active_quiz(
        self, session: AsyncSession, public_id: UUID, fence: UUID
    ) -> Quiz | None:
        quiz = await session.scalar(
            select(Quiz)
            .where(
                Quiz.public_id == public_id,
                Quiz.fence == fence,
                Quiz.status == "processing",
                Quiz.lease_until > datetime.now(UTC),
            )
            .with_for_update()
        )
        if quiz is None or not await snapshot_ready(
            session, quiz.workspace_id, quiz.scope, quiz.profile
        ):
            return None
        return quiz

    async def ensure_quiz_active(self, public_id: UUID, fence: UUID) -> None:
        async with self.sessions.begin() as session:
            if await self._active_quiz(session, public_id, fence) is None:
                raise RagFailure("AGENT_RUN_INACTIVE", "出题任务已停止或资料范围不可用")

    async def quiz_evidence(
        self, public_id: UUID, fence: UUID, topic_vector: list[float] | None = None, topic: str = ""
    ) -> list[Evidence]:
        async with self.sessions.begin() as session:
            quiz = await self._active_quiz(session, public_id, fence)
            if quiz is None:
                return []
            return await quiz_evidence(
                session, quiz.workspace_id, quiz.scope, quiz.profile, topic_vector, topic
            )

    async def finish_quiz(
        self,
        public_id: UUID,
        fence: UUID,
        questions: list[dict[str, Any]],
        usage: dict[str, Any] | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            quiz = await self._active_quiz(session, public_id, fence)
            if quiz is None:
                return
            session.add_all(
                QuizQuestion(
                    workspace_id=quiz.workspace_id, quiz_id=quiz.id, ordinal=ordinal, **question
                )
                for ordinal, question in enumerate(questions, start=1)
            )
            quiz.status = "ready"
            quiz.generation_usage = usage
            quiz.fence = None
            quiz.lease_until = None

    async def fail_quiz(self, public_id: UUID, fence: UUID, code: str) -> None:
        async with self.sessions.begin() as session:
            quiz = await session.scalar(
                select(Quiz)
                .where(
                    Quiz.public_id == public_id, Quiz.fence == fence, Quiz.status == "processing"
                )
                .with_for_update()
            )
            if quiz is not None:
                quiz.status = "failed"
                quiz.failure_code = code
                quiz.fence = None
                quiz.lease_until = None
