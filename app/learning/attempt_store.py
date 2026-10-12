"""Attempts, answers, submission, grading claims and the run continuation they signal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApplicationError
from app.learning.models import (
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
)
from app.learning.quiz_store import load_quiz, question_view
from app.learning.run_models import AgentRun, AgentStageExecution
from app.learning.scope import (
    LEASE_SECONDS,
    missing,
    snapshot_ready,
    workspace_id,
)


class SqlAttemptStore:
    """Attempt persistence; scoring and run continuation commit in one transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], profile: str) -> None:
        self.sessions = sessions
        self.profile = profile

    async def start_attempt(self, principal: UUID, quiz_id: UUID, key: str) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await workspace_id(session, principal)
            quiz = await load_quiz(session, workspace, quiz_id)
            if quiz.status != "ready":
                raise ApplicationError(
                    code="QUIZ_NOT_READY", message="Quiz 尚未生成完成", status_code=409
                )
            existing = await session.scalar(
                select(QuizAttempt).where(
                    QuizAttempt.workspace_id == workspace, QuizAttempt.idempotency_key == key
                )
            )
            if existing is not None:
                if existing.quiz_id != quiz.id:
                    raise ApplicationError(
                        code="IDEMPOTENCY_CONFLICT", message="请求标识已被使用", status_code=409
                    )
                return {"id": str(existing.public_id), "status": existing.status}
            attempt = QuizAttempt(
                workspace_id=workspace, quiz_id=quiz.id, idempotency_key=key, status="in_progress"
            )
            session.add(attempt)
            await session.flush()
            return {"id": str(attempt.public_id), "status": attempt.status}

    async def _attempt(
        self,
        session: AsyncSession,
        workspace: int,
        quiz: Quiz,
        public_id: UUID,
        *,
        lock: bool = False,
    ) -> QuizAttempt:
        query = select(QuizAttempt).where(
            QuizAttempt.workspace_id == workspace,
            QuizAttempt.quiz_id == quiz.id,
            QuizAttempt.public_id == public_id,
        )
        if lock:
            query = query.with_for_update()
        attempt = await session.scalar(query)
        if attempt is None:
            raise missing()
        return attempt

    async def save_answer(
        self,
        principal: UUID,
        quiz_id: UUID,
        attempt_id: UUID,
        question_id: UUID,
        response: Any,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await workspace_id(session, principal)
            quiz = await load_quiz(session, workspace, quiz_id)
            attempt = await self._attempt(session, workspace, quiz, attempt_id, lock=True)
            if attempt.status != "in_progress":
                raise ApplicationError(code="ATTEMPT_CLOSED", message="作答已提交", status_code=409)
            question = await session.scalar(
                select(QuizQuestion).where(
                    QuizQuestion.workspace_id == workspace,
                    QuizQuestion.quiz_id == quiz.id,
                    QuizQuestion.public_id == question_id,
                )
            )
            if question is None:
                raise missing()
            if question.kind == "single" and (
                not isinstance(response, str) or response not in question.options
            ):
                raise ApplicationError(
                    code="ANSWER_INVALID", message="请选择一个有效选项", status_code=422
                )
            if question.kind == "multiple" and (
                not isinstance(response, list)
                or len(response) != len(set(map(str, response)))
                or any(
                    not isinstance(value, str) or value not in question.options
                    for value in response
                )
            ):
                raise ApplicationError(
                    code="ANSWER_INVALID", message="请选择有效选项", status_code=422
                )
            if question.kind == "true_false" and type(response) is not bool:
                raise ApplicationError(
                    code="ANSWER_INVALID", message="请选择正确或错误", status_code=422
                )
            if question.kind == "short" and (
                not isinstance(response, str) or len(response.strip()) > 2000
            ):
                raise ApplicationError(
                    code="ANSWER_INVALID", message="简答最多 2000 字", status_code=422
                )
            answer = await session.scalar(
                select(QuizAnswer).where(
                    QuizAnswer.workspace_id == workspace,
                    QuizAnswer.attempt_id == attempt.id,
                    QuizAnswer.question_id == question.id,
                )
            )
            if expected_revision is not None and expected_revision != attempt.revision:
                # A lost acknowledgement may replay an already saved value without overwriting.
                if answer is not None and answer.response == response:
                    return {
                        "question_id": str(question.public_id),
                        "response": response,
                        "revision": attempt.revision,
                    }
                raise ApplicationError(
                    code="ANSWER_REVISION_CONFLICT",
                    message="作答已在其他页面更新，请重新打开",
                    status_code=409,
                )
            if answer is None:
                answer = QuizAnswer(
                    workspace_id=workspace,
                    attempt_id=attempt.id,
                    question_id=question.id,
                    response=response,
                )
                session.add(answer)
            else:
                answer.response = response
            attempt.revision += 1
            return {
                "question_id": str(question.public_id),
                "response": response,
                "revision": attempt.revision,
            }

    async def _finalize_attempt(self, session: AsyncSession, attempt: QuizAttempt) -> None:
        questions = (
            await session.scalars(
                select(QuizQuestion).where(
                    QuizQuestion.workspace_id == attempt.workspace_id,
                    QuizQuestion.quiz_id == attempt.quiz_id,
                )
            )
        ).all()
        answers = (
            await session.scalars(
                select(QuizAnswer).where(
                    QuizAnswer.workspace_id == attempt.workspace_id,
                    QuizAnswer.attempt_id == attempt.id,
                )
            )
        ).all()
        scores = {answer.question_id: answer.score or 0 for answer in answers}
        attempt.score = round(
            100
            * sum(scores.get(question.id, 0) for question in questions)
            / max(len(questions), 1),
            1,
        )
        attempt.weak_topics = sorted(
            {question.topic for question in questions if scores.get(question.id, 0) < 0.7}
        )
        attempt.status = "submitted"
        attempt.submitted_at = datetime.now(UTC)
        attempt.fence = None
        attempt.lease_until = None
        # Durable continuation is committed with scores; polling/redelivery cannot lose this event.
        await session.execute(
            update(AgentRun)
            .where(
                AgentRun.workspace_id == attempt.workspace_id,
                AgentRun.attempt_id == attempt.id,
                AgentRun.status.in_(["waiting_input", "waiting_result", "running"]),
                AgentRun.stage.in_(["wait", "grade"]),
            )
            .values(
                stage="review",
                status="queued",
                fence=None,
                lease_until=None,
                revision=AgentRun.revision + 1,
            )
        )

    async def submit_attempt(
        self, principal: UUID, quiz_id: UUID, attempt_id: UUID
    ) -> dict[str, Any]:
        from app.learning.domain import score_objective

        async with self.sessions.begin() as session:
            workspace = await workspace_id(session, principal)
            quiz = await load_quiz(session, workspace, quiz_id)
            await session.scalar(
                select(AgentRun)
                .join(
                    QuizAttempt,
                    and_(
                        QuizAttempt.id == AgentRun.attempt_id,
                        QuizAttempt.workspace_id == AgentRun.workspace_id,
                    ),
                )
                .where(AgentRun.workspace_id == workspace, QuizAttempt.public_id == attempt_id)
                .with_for_update(of=AgentRun)
            )
            attempt = await self._attempt(session, workspace, quiz, attempt_id, lock=True)
            if attempt.status != "in_progress":
                return {
                    "id": str(attempt.public_id),
                    "status": attempt.status,
                    "score": attempt.score,
                    "weak_topics": attempt.weak_topics,
                }
            answers = (
                await session.scalars(
                    select(QuizAnswer).where(
                        QuizAnswer.workspace_id == workspace, QuizAnswer.attempt_id == attempt.id
                    )
                )
            ).all()
            questions = {
                question.id: question
                for question in (
                    await session.scalars(
                        select(QuizQuestion).where(
                            QuizQuestion.workspace_id == workspace, QuizQuestion.quiz_id == quiz.id
                        )
                    )
                ).all()
            }
            needs_grading = False
            for answer in answers:
                question = questions[answer.question_id]
                if question.kind == "short":
                    if answer.response.strip():
                        needs_grading = True
                    else:
                        answer.score = 0
                        answer.feedback = "未作答"
                        answer.grading_method = "automatic"
                else:
                    answer.score = score_objective(question.kind, question.answer, answer.response)
                    answer.feedback = "正确" if answer.score == 1 else "请对照来源与解析复习"
                    answer.grading_method = "automatic"
            if needs_grading:
                attempt.status = "grading"
                await session.execute(
                    update(AgentRun)
                    .where(
                        AgentRun.workspace_id == workspace,
                        AgentRun.attempt_id == attempt.id,
                        AgentRun.status == "waiting_input",
                        AgentRun.stage == "wait",
                    )
                    .values(stage="grade", status="queued", revision=AgentRun.revision + 1)
                )
            else:
                await session.flush()
                await self._finalize_attempt(session, attempt)
            return {
                "id": str(attempt.public_id),
                "status": attempt.status,
                "score": attempt.score,
                "weak_topics": attempt.weak_topics,
            }

    async def retry_grading(
        self, principal: UUID, quiz_id: UUID, attempt_id: UUID
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await workspace_id(session, principal)
            quiz = await load_quiz(session, workspace, quiz_id)
            attempt = await self._attempt(session, workspace, quiz, attempt_id, lock=True)
            if attempt.status == "failed" and await session.scalar(
                select(AgentRun.id).where(
                    AgentRun.workspace_id == workspace, AgentRun.attempt_id == attempt.id
                )
            ):
                raise ApplicationError(
                    code="AGENT_RUN_INACTIVE",
                    message="关联任务已停止，请开始新的作答",
                    status_code=409,
                )
            if attempt.status == "failed" and attempt.failure_code in {
                "PROVIDER_TIMEOUT",
                "PROVIDER_LIMIT",
                "PROVIDER_UNAVAILABLE",
                "GRADING_INVALID",
                "GRADING_FAILED",
                "WORKER_INTERRUPTED",
            }:
                if not await snapshot_ready(session, workspace, quiz.scope, quiz.profile):
                    raise ApplicationError(
                        code="SCOPE_NOT_READY", message="资料范围已变化", status_code=409
                    )
                attempt.status = "grading"
                attempt.failure_code = None
                attempt.fence = None
                attempt.lease_until = None
            elif attempt.status not in {"grading", "submitted"}:
                raise ApplicationError(
                    code="GRADING_NOT_RETRYABLE", message="此作答无法重新评分", status_code=409
                )
            return {
                "id": str(attempt.public_id),
                "status": attempt.status,
                "score": attempt.score,
                "weak_topics": attempt.weak_topics,
            }

    async def get_attempt(self, principal: UUID, quiz_id: UUID, attempt_id: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await workspace_id(session, principal)
            quiz = await load_quiz(session, workspace, quiz_id)
            attempt = await self._attempt(session, workspace, quiz, attempt_id)
            questions = (
                await session.scalars(
                    select(QuizQuestion)
                    .where(QuizQuestion.workspace_id == workspace, QuizQuestion.quiz_id == quiz.id)
                    .order_by(QuizQuestion.ordinal)
                )
            ).all()
            answers = (
                await session.scalars(
                    select(QuizAnswer).where(
                        QuizAnswer.workspace_id == workspace, QuizAnswer.attempt_id == attempt.id
                    )
                )
            ).all()
            by_question = {answer.question_id: answer for answer in answers}
            complete = attempt.status == "submitted"
            return {
                "id": str(attempt.public_id),
                "status": attempt.status,
                "score": attempt.score,
                "weak_topics": attempt.weak_topics,
                "revision": attempt.revision,
                "failure_code": attempt.failure_code,
                "questions": [
                    {
                        **question_view(question, show_answer=complete),
                        "response": by_question[question.id].response
                        if question.id in by_question
                        else None,
                        "earned": by_question[question.id].score
                        if complete and question.id in by_question
                        else None,
                        "feedback": by_question[question.id].feedback
                        if complete and question.id in by_question
                        else None,
                        "grading_method": by_question[question.id].grading_method
                        if complete and question.id in by_question
                        else None,
                    }
                    for question in questions
                ],
            }

    async def list_attempts(self, principal: UUID, quiz_id: UUID) -> list[dict[str, Any]]:
        async with self.sessions.begin() as session:
            workspace = await workspace_id(session, principal)
            quiz = await load_quiz(session, workspace, quiz_id)
            attempts = (
                await session.scalars(
                    select(QuizAttempt)
                    .where(QuizAttempt.workspace_id == workspace, QuizAttempt.quiz_id == quiz.id)
                    .order_by(QuizAttempt.created_at.desc())
                    .limit(30)
                )
            ).all()
            return [
                {
                    "id": str(item.public_id),
                    "status": item.status,
                    "score": item.score,
                    "weak_topics": item.weak_topics,
                }
                for item in attempts
            ]

    async def claim_grading(self) -> tuple[UUID, UUID, list[dict[str, Any]]] | None:
        async with self.sessions.begin() as session:
            now = datetime.now(UTC)
            await session.execute(
                update(QuizAttempt)
                .where(
                    QuizAttempt.status == "grading",
                    QuizAttempt.fence.is_not(None),
                    QuizAttempt.lease_until < now,
                    ~exists().where(
                        AgentRun.attempt_id == QuizAttempt.id,
                        AgentRun.status.not_in(
                            ["completed", "failed", "cancelled", "expired", "blocked"]
                        ),
                    ),
                )
                .values(
                    status="failed",
                    fence=None,
                    lease_until=None,
                    failure_code="WORKER_INTERRUPTED",
                )
            )
            attempt = await session.scalar(
                select(QuizAttempt)
                .where(
                    QuizAttempt.status == "grading",
                    QuizAttempt.fence.is_(None),
                    ~exists().where(
                        AgentRun.attempt_id == QuizAttempt.id,
                        AgentRun.status.not_in(
                            ["completed", "failed", "cancelled", "expired", "blocked"]
                        ),
                    ),
                )
                .order_by(QuizAttempt.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if attempt is None:
                return None
            quiz = await session.scalar(
                select(Quiz).where(
                    Quiz.id == attempt.quiz_id, Quiz.workspace_id == attempt.workspace_id
                )
            )
            if quiz is None or not await snapshot_ready(
                session, attempt.workspace_id, quiz.scope, quiz.profile
            ):
                attempt.status = "failed"
                attempt.failure_code = "SCOPE_NOT_READY"
                return None
            attempt.fence = uuid4()
            attempt.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            rows = (
                await session.execute(
                    select(QuizAnswer, QuizQuestion)
                    .join(
                        QuizQuestion,
                        and_(
                            QuizQuestion.id == QuizAnswer.question_id,
                            QuizQuestion.workspace_id == QuizAnswer.workspace_id,
                        ),
                    )
                    .where(
                        QuizAnswer.workspace_id == attempt.workspace_id,
                        QuizAnswer.attempt_id == attempt.id,
                        QuizQuestion.kind == "short",
                        QuizAnswer.score.is_(None),
                    )
                )
            ).all()
            return (
                attempt.public_id,
                attempt.fence,
                [
                    {
                        "question_id": str(question.public_id),
                        "stem": question.stem,
                        "reference": question.answer,
                        "explanation": question.explanation,
                        "response": answer.response,
                        "sources": question.sources,
                    }
                    for answer, question in rows
                ],
            )

    async def finish_grading(
        self, public_id: UUID, fence: UUID, grades: list[dict[str, Any]]
    ) -> None:
        async with self.sessions.begin() as session:
            bound = await session.scalar(
                select(AgentRun)
                .join(
                    QuizAttempt,
                    and_(
                        QuizAttempt.id == AgentRun.attempt_id,
                        QuizAttempt.workspace_id == AgentRun.workspace_id,
                    ),
                )
                .where(QuizAttempt.public_id == public_id)
                .with_for_update(of=AgentRun)
            )
            if (
                bound is not None
                and bound.status not in {"completed", "failed", "cancelled", "expired", "blocked"}
                and (bound.status != "running" or bound.fence != fence)
            ):
                return
            attempt = await session.scalar(
                select(QuizAttempt)
                .where(
                    QuizAttempt.public_id == public_id,
                    QuizAttempt.fence == fence,
                    QuizAttempt.status == "grading",
                    QuizAttempt.lease_until > datetime.now(UTC),
                )
                .with_for_update()
            )
            if attempt is None:
                return
            rows = (
                await session.execute(
                    select(QuizAnswer, QuizQuestion.public_id)
                    .join(
                        QuizQuestion,
                        and_(
                            QuizQuestion.id == QuizAnswer.question_id,
                            QuizQuestion.workspace_id == QuizAnswer.workspace_id,
                        ),
                    )
                    .where(
                        QuizAnswer.workspace_id == attempt.workspace_id,
                        QuizAnswer.attempt_id == attempt.id,
                        QuizQuestion.kind == "short",
                        QuizAnswer.score.is_(None),
                    )
                )
            ).all()
            grade_map = {grade["question_id"]: grade for grade in grades}
            if set(grade_map) != {str(question_id) for _, question_id in rows}:
                raise ValueError("Grading result does not match submitted answers")
            for answer, question_id in rows:
                grade = grade_map[str(question_id)]
                answer.score = grade["score"]
                answer.feedback = grade["feedback"]
                answer.grading_method = "model_hint"
            await session.flush()
            await self._finalize_attempt(session, attempt)
            if bound is not None:
                await session.execute(
                    update(AgentStageExecution)
                    .where(
                        AgentStageExecution.run_id == bound.id, AgentStageExecution.stage == "grade"
                    )
                    .values(status="published", result=None)
                )

    async def fail_grading(self, public_id: UUID, fence: UUID, code: str) -> None:
        async with self.sessions.begin() as session:
            attempt = await session.scalar(
                select(QuizAttempt)
                .where(
                    QuizAttempt.public_id == public_id,
                    QuizAttempt.fence == fence,
                    QuizAttempt.status == "grading",
                )
                .with_for_update()
            )
            if attempt is not None:
                attempt.status = "failed"
                attempt.failure_code = code
                attempt.fence = None
                attempt.lease_until = None
