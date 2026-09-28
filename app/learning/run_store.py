"""Short-transaction workflow persistence; no external call owns a transaction."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApplicationError
from app.learning.conversation_tasks import TaskPlan
from app.learning.models import (
    Conversation,
    ConversationMessage,
    Quiz,
    QuizAttempt,
    QuizDocument,
    QuizQuestion,
)
from app.learning.run_models import AgentRun, AgentStageExecution
from app.learning.scope import missing, retrieve_sources, snapshot_ready
from app.learning.store import LEASE_SECONDS, SqlLearningStore
from app.learning.workflow import (
    GRAPH_VERSION,
    MAX_RUN_CALLS,
    MAX_RUN_COST_UNITS,
    MAX_RUN_SECONDS,
    MAX_RUN_TOKENS,
    TERMINAL_STATUSES,
    check_run_budget,
)
from app.rag.domain import Evidence, RagFailure
from app.rag.messages import FAILURES


def run_view(run: AgentRun) -> dict[str, Any]:
    return {
        "id": str(run.public_id),
        "status": run.status,
        "stage": run.stage,
        "revision": run.revision,
        "scope": run.scope,
        "graph_version": run.graph_version,
        "plan": run.plan,
        "outputs": run.outputs,
        "clarification": run.clarification,
        "failure_code": run.failure_code,
        "failure_message": FAILURES.get(run.failure_code or ""),
        "expires_at": run.expires_at,
    }


class SqlAgentRunStore:
    def __init__(self, learning: SqlLearningStore) -> None:
        self.learning = learning
        self.sessions = learning.sessions

    async def _owned(self, session: AsyncSession, workspace: int, public_id: UUID) -> AgentRun:
        run = await session.scalar(
            select(AgentRun)
            .where(AgentRun.workspace_id == workspace, AgentRun.public_id == public_id)
            .with_for_update()
        )
        if run is None:
            raise missing()
        return run

    async def get(self, principal: UUID, public_id: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self.learning._workspace(session, principal)
            run = await self._owned(session, workspace, public_id)
            if run.status not in TERMINAL_STATUSES and run.expires_at <= datetime.now(UTC):
                self._close(run, "expired", "RUN_EXPIRED")
                await self._fail_message(session, run, "RUN_EXPIRED")
                await self._stop_grading(session, run, "RUN_EXPIRED")
                await session.execute(
                    delete(AgentStageExecution).where(
                        AgentStageExecution.workspace_id == workspace,
                        AgentStageExecution.run_id == run.id,
                    )
                )
            elif run.status not in TERMINAL_STATUSES and not await snapshot_ready(
                session, workspace, run.scope, run.profile
            ):
                self._close(run, "blocked", "RUN_SOURCE_CHANGED")
                await self._fail_message(session, run, "RUN_SOURCE_CHANGED")
                await self._stop_grading(session, run, "RUN_SOURCE_CHANGED")
            return run_view(run)

    async def list_runs(
        self, principal: UUID, conversation: UUID, cursor: int, limit: int
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self.learning._workspace(session, principal)
            item = await self.learning._conversation(session, workspace, conversation)
            runs = (
                await session.scalars(
                    select(AgentRun)
                    .where(
                        AgentRun.workspace_id == workspace,
                        AgentRun.conversation_id == item.id,
                        AgentRun.id > cursor,
                    )
                    .order_by(AgentRun.id)
                    .limit(limit + 1)
                )
            ).all()
            return {
                "items": [run_view(run) for run in runs[:limit]],
                "next_cursor": runs[limit - 1].id if len(runs) > limit else None,
            }

    def _close(self, run: AgentRun, status: str, code: str | None = None) -> None:
        run.status, run.failure_code = status, code
        run.fence, run.lease_until = None, None
        run.quiz_reserved = False
        run.revision += 1

    async def cancel(self, principal: UUID, public_id: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self.learning._workspace(session, principal)
            run = await self._owned(session, workspace, public_id)
            if run.status not in TERMINAL_STATUSES:
                self._close(run, "cancelled")
                message = await session.get(ConversationMessage, run.message_id)
                assert message is not None
                if message.status in ("queued", "processing"):
                    message.status, message.fence, message.lease_until = "cancelled", None, None
                await self._stop_grading(session, run, "AGENT_RUN_INACTIVE")
                await session.execute(
                    delete(AgentStageExecution).where(
                        AgentStageExecution.workspace_id == workspace,
                        AgentStageExecution.run_id == run.id,
                    )
                )
            return run_view(run)

    async def respond(
        self, principal: UUID, public_id: UUID, key: str, revision: int, answer: str
    ) -> dict[str, Any]:
        if not 2 <= len(answer.strip()) <= 1200:
            raise ApplicationError(
                code="RUN_INPUT_INVALID", message="请填写有效的补充信息", status_code=422
            )
        async with self.sessions.begin() as session:
            workspace = await self.learning._workspace(session, principal)
            run = await self._owned(session, workspace, public_id)
            if run.response_key == key:
                if run.response != answer:
                    raise ApplicationError(
                        code="IDEMPOTENCY_CONFLICT", message="请求标识已被使用", status_code=409
                    )
                return run_view(run)
            if run.status != "waiting_input" or run.stage != "clarify":
                raise ApplicationError(
                    code="RUN_CLOSED", message="任务当前不接受补充信息", status_code=409
                )
            if run.revision != revision:
                raise ApplicationError(
                    code="RUN_REVISION_CONFLICT", message="任务已更新，请重新打开", status_code=409
                )
            if run.expires_at <= datetime.now(UTC) or not await snapshot_ready(
                session, workspace, run.scope, run.profile
            ):
                raise ApplicationError(
                    code="RUN_SOURCE_CHANGED",
                    message="资料或任务已过期，请重新开始",
                    status_code=409,
                )
            run.response, run.response_key, run.clarification = answer, key, None
            run.stage = "replan"  # A second clarification terminates instead of an unbounded loop.
            run.status, run.revision = "queued", run.revision + 1
            return run_view(run)

    async def claim(self) -> dict[str, Any] | None:
        async with self.sessions.begin() as session:
            now = datetime.now(UTC)
            expired = (
                await session.scalars(
                    select(AgentRun)
                    .where(
                        AgentRun.status.not_in(list(TERMINAL_STATUSES)),
                        AgentRun.expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(50)
                )
            ).all()
            for expired_run in expired:
                self._close(expired_run, "expired", "RUN_EXPIRED")
                await self._fail_message(session, expired_run, "RUN_EXPIRED")
                await self._stop_grading(session, expired_run, "RUN_EXPIRED")
                await session.execute(
                    delete(AgentStageExecution).where(AgentStageExecution.run_id == expired_run.id)
                )
            stale = (
                await session.scalars(
                    select(AgentRun)
                    .where(
                        AgentRun.status == "running",
                        AgentRun.lease_until < now,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(50)
                )
            ).all()
            for stale_run in stale:
                unknown = await session.scalar(
                    select(AgentStageExecution.id)
                    .where(
                        AgentStageExecution.run_id == stale_run.id,
                        AgentStageExecution.status == "calling",
                    )
                    .limit(1)
                )
                if unknown:
                    self._close(stale_run, "blocked", "RUN_RESULT_UNKNOWN")
                    await self._fail_message(session, stale_run, "RUN_RESULT_UNKNOWN")
                    await self._stop_grading(session, stale_run, "RUN_RESULT_UNKNOWN")
                else:
                    reservation = stale_run.quiz_reserved
                    self._close(stale_run, "queued")
                    stale_run.quiz_reserved = reservation
            # Conversation row is the per-conversation scheduler lock, shared with ask().
            candidates = (
                await session.scalars(
                    select(AgentRun)
                    .where(
                        AgentRun.status == "queued",
                        AgentRun.profile == self.learning.profile,
                    )
                    .order_by(AgentRun.created_at)
                    .limit(20)
                )
            ).all()
            for candidate in candidates:
                conversation = await session.scalar(
                    select(Conversation)
                    .where(
                        Conversation.id == candidate.conversation_id,
                        Conversation.workspace_id == candidate.workspace_id,
                    )
                    .with_for_update(skip_locked=True)
                )
                if conversation is None:
                    continue
                run = await session.scalar(
                    select(AgentRun)
                    .where(AgentRun.id == candidate.id, AgentRun.status == "queued")
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
                if run is None:
                    continue
                busy = await session.scalar(
                    select(AgentRun.id)
                    .where(
                        AgentRun.workspace_id == run.workspace_id,
                        AgentRun.conversation_id == run.conversation_id,
                        AgentRun.status == "running",
                    )
                    .limit(1)
                )
                if busy:
                    continue
                if run.graph_version != GRAPH_VERSION:
                    self._close(run, "blocked", "RUN_VERSION_UNSUPPORTED")
                    await self._fail_message(session, run, "RUN_VERSION_UNSUPPORTED")
                    await self._stop_grading(session, run, "RUN_VERSION_UNSUPPORTED")
                    continue
                if not await snapshot_ready(session, run.workspace_id, run.scope, run.profile):
                    self._close(run, "blocked", "RUN_SOURCE_CHANGED")
                    await self._fail_message(session, run, "RUN_SOURCE_CHANGED")
                    await self._stop_grading(session, run, "RUN_SOURCE_CHANGED")
                    continue
                if run.usage.get("execution_seconds", 0) + 110 > MAX_RUN_SECONDS:
                    self._close(run, "failed", "RUN_BUDGET_EXCEEDED")
                    await self._fail_message(session, run, "RUN_BUDGET_EXCEEDED")
                    await self._stop_grading(session, run, "RUN_BUDGET_EXCEEDED")
                    continue
                run.usage = {
                    **run.usage,
                    "execution_seconds": run.usage.get("execution_seconds", 0) + 110,
                }
                run.status, run.fence = "running", uuid4()
                run.lease_until = now + timedelta(seconds=LEASE_SECONDS)
                run.revision += 1
                message = await session.get(ConversationMessage, run.message_id)
                assert message is not None
                if message.status in ("queued", "processing"):
                    message.status, message.fence, message.lease_until = (
                        "processing",
                        run.fence,
                        run.lease_until,
                    )
                previous = (
                    await session.scalars(
                        select(ConversationMessage)
                        .where(
                            ConversationMessage.workspace_id == run.workspace_id,
                            ConversationMessage.conversation_id == run.conversation_id,
                            ConversationMessage.id < message.id,
                            ConversationMessage.status == "answered",
                            ConversationMessage.scope == run.scope,
                        )
                        .order_by(ConversationMessage.id.desc())
                        .limit(3)
                    )
                ).all()
                history = [
                    {
                        "question": m.question,
                        "answer": (
                            " ".join(c["text"] for c in m.answer.get("claims", []))
                            if m.answer
                            else (m.task_result or {}).get("text", "")
                        )[:600],
                    }
                    for m in reversed(previous)
                ]
                return {
                    **run_view(run),
                    "fence": run.fence,
                    "workspace_id": run.workspace_id,
                    "message_id": message.public_id,
                    "request": message.question,
                    "response": run.response,
                    "usage": run.usage,
                    "history": history,
                }
            return None

    async def _active(self, session: AsyncSession, public_id: UUID, fence: UUID) -> AgentRun:
        run = await session.scalar(
            select(AgentRun)
            .where(
                AgentRun.public_id == public_id,
                AgentRun.fence == fence,
                AgentRun.status == "running",
                AgentRun.lease_until > datetime.now(UTC),
                AgentRun.expires_at > datetime.now(UTC),
                AgentRun.graph_version == GRAPH_VERSION,
            )
            .with_for_update()
        )
        if run is None or not await snapshot_ready(
            session, run.workspace_id, run.scope, run.profile
        ):
            raise RagFailure("AGENT_RUN_INACTIVE", "任务已停止或资料范围不可用")
        return run

    async def ensure_active(self, public_id: UUID, fence: UUID) -> None:
        async with self.sessions.begin() as session:
            await self._active(session, public_id, fence)

    async def sources(
        self, public_id: UUID, fence: UUID, vector: list[float], query: str
    ) -> list[Evidence]:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            return await retrieve_sources(
                session, run.workspace_id, run.scope, run.profile, vector, query
            )

    async def begin_call(self, public_id: UUID, fence: UUID, ordinal: int, kind: str) -> Any:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            existing = await session.scalar(
                select(AgentStageExecution).where(
                    AgentStageExecution.run_id == run.id,
                    AgentStageExecution.stage == run.stage,
                    AgentStageExecution.ordinal == ordinal,
                )
            )
            if existing is not None:
                if existing.kind != kind or existing.status != "returned":
                    raise RagFailure("RUN_RESULT_UNKNOWN", "外部调用结果未确认")
                return existing.result
            usage = run.usage
            if kind != "embed" and (
                usage.get("model_calls", 0) >= MAX_RUN_CALLS
                or usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0) + 18_000
                > MAX_RUN_TOKENS
                or usage.get("cost_units", 0) + 30_000 > MAX_RUN_COST_UNITS
            ):
                raise RagFailure("RUN_BUDGET_EXCEEDED", "剩余预算不足以安全发起调用")
            if kind == "embed" and usage.get("embedding_calls", 0) >= 8:
                raise RagFailure("RUN_BUDGET_EXCEEDED", "向量调用达到上限")
            session.add(
                AgentStageExecution(
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    stage=run.stage,
                    ordinal=ordinal,
                    kind=kind,
                    status="calling",
                )
            )
            return None

    async def finish_call(
        self, public_id: UUID, fence: UUID, ordinal: int, result: Any, usage: dict[str, Any]
    ) -> None:
        if len(json.dumps(result, ensure_ascii=False)) > 150_000:
            raise RagFailure("AGENT_CONTEXT_LIMIT", "调用结果超过检查点上限")
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            call = await session.scalar(
                select(AgentStageExecution)
                .where(
                    AgentStageExecution.run_id == run.id,
                    AgentStageExecution.stage == run.stage,
                    AgentStageExecution.ordinal == ordinal,
                )
                .with_for_update()
            )
            assert call is not None
            if call.status == "returned":
                return
            call.result, call.usage, call.status = result, usage, "returned"
            total = dict(run.usage)
            for field in (
                "model_calls",
                "prompt_tokens",
                "completion_tokens",
                "cost_units",
                "embedding_calls",
            ):
                total[field] = total.get(field, 0) + usage.get(field, 0)
            run.usage = total
            # Persist actual usage even when it exceeds a cap. Publication checks the cap.

    async def reject_call(self, public_id: UUID, fence: UUID, ordinal: int) -> None:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            await session.execute(
                update(AgentStageExecution)
                .where(
                    AgentStageExecution.run_id == run.id,
                    AgentStageExecution.stage == run.stage,
                    AgentStageExecution.ordinal == ordinal,
                )
                .values(status="published", result=None)
            )

    async def finish_grading(
        self, public_id: UUID, fence: UUID, grades: list[dict[str, Any]]
    ) -> None:
        await self.learning.finish_grading(public_id, fence, grades)

    async def _advance(self, session: AsyncSession, run: AgentRun, stage: str, status: str) -> None:
        await session.execute(
            update(AgentStageExecution)
            .where(AgentStageExecution.run_id == run.id, AgentStageExecution.stage == run.stage)
            .values(status="published", result=None)
        )
        run.stage = stage
        self._close(run, status)

    async def save_plan(self, public_id: UUID, fence: UUID, plan: TaskPlan) -> None:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            check_run_budget(run.usage)
            message = await session.get(ConversationMessage, run.message_id)
            assert message is not None
            run.plan = plan.model_dump()
            if plan.action == "clarify":
                run.clarification = plan.message
                message.task_result = {"kind": "clarification", "text": plan.message}
                message.status = "answered"
                second = run.stage == "replan"
                await self._advance(
                    session, run, "clarify", "completed" if second else "waiting_input"
                )
            else:
                stage = (
                    "summary"
                    if plan.action in ("answer", "study")
                    else (
                        "review"
                        if plan.action in ("review_mistakes", "practice_weak_topics")
                        else "quiz"
                    )
                )
                await self._advance(session, run, stage, "queued")
                if message.status == "answered":
                    message.task_result = None
                    message.status = "queued"

    async def reserve_quiz(self, public_id: UUID, fence: UUID) -> None:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            if not run.quiz_reserved:
                await self.learning._check_quiz_capacity(session, run.workspace_id)
                run.quiz_reserved = True

    async def publish_summary(
        self, public_id: UUID, fence: UUID, answer: dict[str, Any], usage: dict[str, Any]
    ) -> None:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            check_run_budget(run.usage)
            message = await session.get(ConversationMessage, run.message_id)
            assert message is not None
            message.answer, message.usage = answer, usage
            message.status = "insufficient" if answer["insufficient_evidence"] else "answered"
            message.fence, message.lease_until = None, None
            run.outputs = [
                *run.outputs,
                {
                    "kind": "summary",
                    "message_id": str(message.public_id),
                    "text": "资料依据不足" if answer["insufficient_evidence"] else "已完成资料总结",
                },
            ]
            study = (run.plan or {}).get("action") == "study"
            await self._advance(
                session,
                run,
                "quiz" if study else "done",
                "queued" if study and not answer["insufficient_evidence"] else "completed",
            )

    async def publish_quiz(
        self,
        public_id: UUID,
        fence: UUID,
        config: dict[str, Any],
        questions: list[dict[str, Any]],
        usage: dict[str, Any],
    ) -> None:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            check_run_budget(run.usage)
            assert run.quiz_reserved
            title = (run.plan or {}).get("title") or "学习练习"
            quiz = Quiz(
                workspace_id=run.workspace_id,
                idempotency_key=f"run:{public_id}:{run.stage}",
                title=title,
                config=config,
                requested_scope={
                    "document_ids": sorted(item["document_id"] for item in run.scope),
                    "collection_ids": [],
                },
                scope=run.scope,
                profile=run.profile,
                status="ready",
                generation_usage=usage,
            )
            session.add(quiz)
            await session.flush()
            session.add_all(
                QuizDocument(
                    workspace_id=run.workspace_id,
                    quiz_id=quiz.id,
                    document_version_id=item["version_id"],
                )
                for item in run.scope
            )
            session.add_all(
                QuizQuestion(
                    workspace_id=run.workspace_id, quiz_id=quiz.id, ordinal=ordinal, **question
                )
                for ordinal, question in enumerate(questions, 1)
            )
            attempt = QuizAttempt(
                workspace_id=run.workspace_id,
                quiz_id=quiz.id,
                idempotency_key=f"run:{public_id}:{run.stage}:attempt",
                status="in_progress",
            )
            session.add(attempt)
            await session.flush()
            count = sum(config["type_counts"].values())
            text = f"已生成 {len(questions)} / {count} 题。"
            if len(questions) < count:
                text += "现有资料不足以支持其余合格题目。"
            output = {
                "kind": "quiz",
                "quiz_id": str(quiz.public_id),
                "attempt_id": str(attempt.public_id),
                "title": title,
                "text": text,
            }
            run.outputs = [*run.outputs, output]
            message = await session.get(ConversationMessage, run.message_id)
            assert message is not None
            study = (run.plan or {}).get("action") == "study"
            if not study:
                message.task_result, message.quiz_id, message.usage = output, quiz.id, usage
            message.status, message.fence, message.lease_until = "answered", None, None
            # A weak-topic Quiz is the final output; never recursively schedule more practice.
            wait = (
                study and run.stage == "quiz" and (run.plan or {}).get("review_after_submit", False)
            )
            if wait:
                run.quiz_id, run.attempt_id = quiz.id, attempt.id
            await self._advance(
                session, run, "wait" if wait else "done", "waiting_input" if wait else "completed"
            )

    async def review(self, public_id: UUID, fence: UUID) -> dict[str, Any] | None:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            if run.attempt_id is None:
                row = (
                    await session.execute(
                        select(Quiz, QuizAttempt)
                        .join(
                            QuizAttempt,
                            and_(
                                QuizAttempt.quiz_id == Quiz.id,
                                QuizAttempt.workspace_id == Quiz.workspace_id,
                            ),
                        )
                        .where(
                            Quiz.workspace_id == run.workspace_id,
                            Quiz.scope == run.scope,
                            QuizAttempt.workspace_id == run.workspace_id,
                            QuizAttempt.status == "submitted",
                        )
                        .order_by(QuizAttempt.submitted_at.desc(), QuizAttempt.id.desc())
                        .limit(1)
                    )
                ).first()
            else:
                row = (
                    await session.execute(
                        select(Quiz, QuizAttempt)
                        .join(
                            QuizAttempt,
                            and_(
                                QuizAttempt.quiz_id == Quiz.id,
                                QuizAttempt.workspace_id == Quiz.workspace_id,
                            ),
                        )
                        .where(
                            Quiz.workspace_id == run.workspace_id,
                            Quiz.scope == run.scope,
                            QuizAttempt.workspace_id == run.workspace_id,
                            QuizAttempt.id == run.attempt_id,
                            QuizAttempt.status == "submitted",
                        )
                    )
                ).first()
            if row is None:
                return None
            quiz, attempt = row
            return {
                "kind": "review",
                "quiz_id": str(quiz.public_id),
                "attempt_id": str(attempt.public_id),
                "title": quiz.title,
                "weak_topics": attempt.weak_topics or [],
                "text": "以下为真实作答的评分、错题解析与来源。",
            }

    async def publish_review(
        self, public_id: UUID, fence: UUID, review: dict[str, Any] | None
    ) -> None:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            plan = run.plan or {}
            message = await session.get(ConversationMessage, run.message_id)
            assert message is not None
            if review:
                output = {k: v for k, v in review.items() if k != "weak_topics"}
                run.outputs = [*run.outputs, output]
            else:
                output = {
                    "kind": "clarification",
                    "text": "当前资料范围还没有已完成练习，请先生成 Quiz 并提交作答。",
                }
            if plan.get("action") != "study":
                message.task_result = output
                message.status, message.fence, message.lease_until = "answered", None, None
            practice = plan.get("action") == "practice_weak_topics" or plan.get(
                "practice_after_review"
            )
            if practice and review and review["weak_topics"]:
                plan = {**plan, "weak_topics": review["weak_topics"]}
                run.plan = plan
                await self._advance(session, run, "weak", "queued")
            else:
                if practice and review:
                    run.outputs = [
                        *run.outputs,
                        {"kind": "notice", "text": "本次练习未发现需要复习的薄弱点。"},
                    ]
                await self._advance(session, run, "done", "completed")

    async def grading_items(
        self, public_id: UUID, fence: UUID
    ) -> tuple[UUID, list[dict[str, Any]]]:
        from app.learning.models import QuizAnswer

        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            attempt = await session.scalar(
                select(QuizAttempt)
                .where(
                    QuizAttempt.workspace_id == run.workspace_id,
                    QuizAttempt.id == run.attempt_id,
                    QuizAttempt.status == "grading",
                )
                .with_for_update()
            )
            if attempt is None:
                raise RagFailure("RUN_CLOSED", "评分记录不可用")
            attempt.fence, attempt.lease_until = fence, run.lease_until
            rows = (
                await session.execute(
                    select(QuizQuestion, QuizAnswer)
                    .join(
                        QuizAnswer,
                        and_(
                            QuizAnswer.question_id == QuizQuestion.id,
                            QuizAnswer.workspace_id == QuizQuestion.workspace_id,
                        ),
                    )
                    .where(
                        QuizQuestion.workspace_id == run.workspace_id,
                        QuizQuestion.quiz_id == run.quiz_id,
                        QuizAnswer.attempt_id == attempt.id,
                        QuizQuestion.kind == "short",
                        QuizAnswer.score.is_(None),
                    )
                )
            ).all()
            return attempt.public_id, [
                {
                    "question_id": str(q.public_id),
                    "stem": q.stem,
                    "reference": q.answer,
                    "response": a.response,
                    "sources": q.sources,
                }
                for q, a in rows
            ]

    async def _fail_message(self, session: AsyncSession, run: AgentRun, code: str) -> None:
        message = await session.get(ConversationMessage, run.message_id)
        if message and message.status in ("queued", "processing"):
            message.status, message.failure_code = "failed", code
            message.fence, message.lease_until = None, None

    async def _stop_grading(self, session: AsyncSession, run: AgentRun, code: str) -> None:
        if run.stage == "grade" and run.attempt_id is not None:
            await session.execute(
                update(QuizAttempt)
                .where(
                    QuizAttempt.workspace_id == run.workspace_id,
                    QuizAttempt.id == run.attempt_id,
                    QuizAttempt.status == "grading",
                )
                .values(status="failed", failure_code=code, fence=None, lease_until=None)
            )

    async def fail(self, public_id: UUID, fence: UUID, code: str) -> None:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.public_id == public_id,
                    AgentRun.fence == fence,
                    AgentRun.status == "running",
                )
                .with_for_update()
            )
            if run is not None:
                unknown = await session.scalar(
                    select(AgentStageExecution.id)
                    .where(
                        AgentStageExecution.run_id == run.id,
                        AgentStageExecution.status == "calling",
                    )
                    .limit(1)
                )
                self._close(
                    run,
                    "blocked" if unknown else "failed",
                    "RUN_RESULT_UNKNOWN" if unknown else code,
                )
                await self._fail_message(session, run, run.failure_code or code)
                await self._stop_grading(session, run, run.failure_code or code)
                if not unknown:
                    await session.execute(
                        update(AgentStageExecution)
                        .where(AgentStageExecution.run_id == run.id)
                        .values(status="published", result=None)
                    )

    async def record_duration(self, public_id: UUID, seconds: float) -> None:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(AgentRun).where(AgentRun.public_id == public_id).with_for_update()
            )
            if run:
                run.usage = {
                    **run.usage,
                    "observed_seconds": run.usage.get("observed_seconds", 0) + seconds,
                }
