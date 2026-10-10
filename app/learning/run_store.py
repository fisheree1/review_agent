"""Short-transaction workflow persistence; no external call owns a transaction."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApplicationError
from app.documents.infrastructure.models import DocumentModel
from app.learning.conversation_tasks import TaskPlan
from app.learning.graph_tasks import GraphTaskPlan
from app.learning.models import (
    Conversation,
    ConversationMessage,
    Quiz,
    QuizAttempt,
    QuizDocument,
    QuizQuestion,
)
from app.learning.overview import (
    OVERVIEW_VERSION,
    OverviewChunk,
    merge_overview_answers,
    overview_batch_index,
    overview_batches,
    select_overview_chunks,
)
from app.learning.pdf_export import PDF_EXPORT_VERSION
from app.learning.run_models import AgentRun, AgentStageExecution
from app.learning.scope import missing, retrieve_sources, snapshot_ready
from app.learning.store import LEASE_SECONDS, SqlLearningStore
from app.learning.workflow import (
    BATCHED_GRAPH_VERSIONS,
    COMPOSABLE_GRAPH_VERSIONS,
    MAX_RUN_CALLS,
    MAX_RUN_COST_UNITS,
    MAX_RUN_TOKENS,
    SUPPORTED_GRAPH_VERSIONS,
    TERMINAL_STATUSES,
    check_run_budget,
    run_seconds_limit,
)
from app.rag.domain import ANSWER_VALIDATION_REASONS, Evidence, RagFailure
from app.rag.messages import FAILURES
from app.rag.models import DocumentChunk, DocumentIndex


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

    async def pdf_material(
        self, principal: UUID, public_id: UUID
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]], dict[str, int] | None]:
        async with self.sessions.begin() as session:
            workspace = await self.learning._workspace(session, principal)
            run = await self._owned(session, workspace, public_id)
            if not any(output.get("kind") == "pdf" for output in run.outputs):
                raise missing()
            if not await snapshot_ready(session, workspace, run.scope, run.profile):
                raise missing()
            message = await session.get(ConversationMessage, run.message_id)
            if message is None or not message.answer or message.answer.get("insufficient_evidence"):
                raise missing()
            summary = next(
                (output for output in run.outputs if output.get("kind") == "summary"), {}
            )
            subject = ((run.plan or {}).get("summary_request") or "").strip()
            return (
                subject[:80] if subject else "知识点整理",
                message.answer,
                run.scope,
                summary.get("coverage"),
            )

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
                if run.graph_version not in SUPPORTED_GRAPH_VERSIONS:
                    self._close(run, "blocked", "RUN_VERSION_UNSUPPORTED")
                    await self._fail_message(session, run, "RUN_VERSION_UNSUPPORTED")
                    await self._stop_grading(session, run, "RUN_VERSION_UNSUPPORTED")
                    continue
                if not await snapshot_ready(session, run.workspace_id, run.scope, run.profile):
                    self._close(run, "blocked", "RUN_SOURCE_CHANGED")
                    await self._fail_message(session, run, "RUN_SOURCE_CHANGED")
                    await self._stop_grading(session, run, "RUN_SOURCE_CHANGED")
                    continue
                if run.usage.get("execution_seconds", 0) + 110 > run_seconds_limit(
                    run.graph_version
                ):
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
                memory = conversation.memory or {}
                memory_summary = (
                    str(memory.get("summary", ""))[:700]
                    if run.graph_version in COMPOSABLE_GRAPH_VERSIONS
                    and memory.get("scope") == run.scope
                    else ""
                )
                return {
                    **run_view(run),
                    "fence": run.fence,
                    "workspace_id": run.workspace_id,
                    "message_id": message.public_id,
                    "request": message.question,
                    "response": run.response,
                    "usage": run.usage,
                    "history": history,
                    "memory_summary": memory_summary,
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
                AgentRun.graph_version.in_(SUPPORTED_GRAPH_VERSIONS),
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

    async def overview_sources(
        self, public_id: UUID, fence: UUID
    ) -> tuple[list[Evidence], dict[str, int]]:
        """Sample selected active versions across their entire indexed page range."""
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            per_document = max(1, 15 // len(run.scope))
            sources: list[Evidence] = []
            total_pages = 0
            sampled_pages: set[tuple[int, int]] = set()
            for item in run.scope:
                index_id = await session.scalar(
                    select(DocumentIndex.id)
                    .join(
                        DocumentModel,
                        and_(
                            DocumentModel.active_version_id == DocumentIndex.document_version_id,
                            DocumentModel.workspace_id == DocumentIndex.workspace_id,
                        ),
                    )
                    .where(
                        DocumentIndex.workspace_id == run.workspace_id,
                        DocumentIndex.document_version_id == item["version_id"],
                        DocumentIndex.profile == run.profile,
                        DocumentIndex.status == "ready",
                        DocumentModel.public_id == UUID(item["document_id"]),
                        DocumentModel.status == "ready",
                        DocumentModel.deleted_at.is_(None),
                    )
                )
                if index_id is None:
                    raise RagFailure("RUN_SOURCE_CHANGED", "资料索引已变化")
                page_count = await session.scalar(
                    select(func.count(func.distinct(DocumentChunk.unit))).where(
                        DocumentChunk.workspace_id == run.workspace_id,
                        DocumentChunk.index_id == index_id,
                    )
                )
                total_pages += page_count or 0
                count = await session.scalar(
                    select(func.count(DocumentChunk.id)).where(
                        DocumentChunk.workspace_id == run.workspace_id,
                        DocumentChunk.index_id == index_id,
                    )
                )
                if not count:
                    continue
                sample_count = min(per_document, count)
                offsets = sorted(
                    {
                        min(count - 1, (2 * index + 1) * count // (2 * sample_count))
                        for index in range(sample_count)
                    }
                )
                for offset in offsets:
                    chunk = await session.scalar(
                        select(DocumentChunk)
                        .where(
                            DocumentChunk.workspace_id == run.workspace_id,
                            DocumentChunk.index_id == index_id,
                        )
                        .order_by(DocumentChunk.unit, DocumentChunk.ordinal)
                        .offset(offset)
                        .limit(1)
                    )
                    if chunk is None:
                        continue
                    sampled_pages.add((item["version_id"], chunk.unit))
                    sources.append(
                        Evidence(
                            chunk.public_id,
                            chunk.content,
                            chunk.unit,
                            {"kind": "page", "position": chunk.unit, "title": None, "path": []},
                            document_id=UUID(item["document_id"]),
                            version_id=item["version_id"],
                        )
                    )
            return sources, {"sampled_pages": len(sampled_pages), "indexed_pages": total_pages}

    async def overview_batch(
        self, public_id: UUID, fence: UUID
    ) -> tuple[list[Evidence], dict[str, int]]:
        """Read only the current bounded batch from immutable, currently active indexes."""
        from sqlalchemy import or_

        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            batch_index = overview_batch_index(run.stage)
            rows = (
                await session.execute(
                    select(
                        DocumentChunk.public_id,
                        DocumentModel.public_id,
                        DocumentIndex.document_version_id,
                        DocumentChunk.unit,
                        DocumentChunk.ordinal,
                    )
                    .join(
                        DocumentIndex,
                        and_(
                            DocumentIndex.id == DocumentChunk.index_id,
                            DocumentIndex.workspace_id == DocumentChunk.workspace_id,
                        ),
                    )
                    .join(
                        DocumentModel,
                        and_(
                            DocumentModel.active_version_id == DocumentIndex.document_version_id,
                            DocumentModel.workspace_id == DocumentIndex.workspace_id,
                        ),
                    )
                    .where(
                        DocumentChunk.workspace_id == run.workspace_id,
                        DocumentIndex.profile == run.profile,
                        DocumentIndex.status == "ready",
                        DocumentModel.status == "ready",
                        DocumentModel.deleted_at.is_(None),
                        or_(
                            *(
                                and_(
                                    DocumentModel.public_id == UUID(item["document_id"]),
                                    DocumentIndex.document_version_id == item["version_id"],
                                )
                                for item in run.scope
                            )
                        ),
                    )
                )
            ).all()
            metadata = [OverviewChunk(*row) for row in rows]
            batches = overview_batches(select_overview_chunks(metadata))
            selected = batches[batch_index] if batches else []
            read = [chunk for batch in batches[: batch_index + 1] for chunk in batch]
            page_totals: dict[tuple[int, int], int] = {}
            page_read: dict[tuple[int, int], int] = {}
            for chunk in metadata:
                page = (chunk.version_id, chunk.unit)
                page_totals[page] = page_totals.get(page, 0) + 1
            for chunk in read:
                page = (chunk.version_id, chunk.unit)
                page_read[page] = page_read.get(page, 0) + 1
            bodies = (
                list(
                    await session.scalars(
                        select(DocumentChunk).where(
                            DocumentChunk.workspace_id == run.workspace_id,
                            DocumentChunk.public_id.in_([chunk.id for chunk in selected]),
                        )
                    )
                )
                if selected
                else []
            )
            by_id = {chunk.public_id: chunk for chunk in bodies}
            if len(bodies) != len(selected):
                raise RagFailure("RUN_SOURCE_CHANGED", "资料索引已变化")
            sources = [
                Evidence(
                    chunk.id,
                    by_id[chunk.id].content,
                    chunk.unit,
                    {"kind": "page", "position": chunk.unit, "title": None, "path": []},
                    document_id=chunk.document_id,
                    version_id=chunk.version_id,
                )
                for chunk in selected
            ]
            return sources, {
                "sampled_pages": len(page_read),
                "indexed_pages": len(page_totals),
                "full_pages": sum(
                    page_read.get(page, 0) == count for page, count in page_totals.items()
                ),
                "processed_chunks": len(read),
                "indexed_chunks": len(metadata),
                "completed_batches": batch_index + 1,
                "total_batches": max(1, len(batches)),
            }

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

    async def record_answer_validation(
        self, public_id: UUID, fence: UUID, attempt: int, diagnosis: dict[str, Any]
    ) -> None:
        reason = diagnosis.get("reason")
        if (
            type(attempt) is not int
            or attempt not in (0, 1)
            or not isinstance(reason, str)
            or reason not in ANSWER_VALIDATION_REASONS
            or set(diagnosis) - {"reason", "point", "citation", "length"}
            or any(
                type(value) is not int or not 0 <= value <= 150_000
                for key, value in diagnosis.items()
                if key != "reason"
            )
        ):
            raise RagFailure("ANSWER_INVALID", "校验诊断格式错误")
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            issues = list(run.usage.get("answer_validation", []))
            if not any(
                item["stage"] == run.stage and item["attempt"] == attempt for item in issues
            ):
                issues.append({"stage": run.stage, "attempt": attempt, **diagnosis})
                run.usage = {**run.usage, "answer_validation": issues[-16:]}

    async def _advance(self, session: AsyncSession, run: AgentRun, stage: str, status: str) -> None:
        await session.execute(
            update(AgentStageExecution)
            .where(AgentStageExecution.run_id == run.id, AgentStageExecution.stage == run.stage)
            .values(status="published", result=None)
        )
        run.stage = stage
        self._close(run, status)

    async def save_plan(self, public_id: UUID, fence: UUID, plan: TaskPlan | GraphTaskPlan) -> None:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            check_run_budget(run.usage, run.graph_version)
            message = await session.get(ConversationMessage, run.message_id)
            assert message is not None
            run.plan = plan.model_dump()
            if isinstance(plan, GraphTaskPlan):
                conversation = await session.scalar(
                    select(Conversation)
                    .where(
                        Conversation.id == run.conversation_id,
                        Conversation.workspace_id == run.workspace_id,
                    )
                    .with_for_update()
                )
                assert conversation is not None
                if plan.memory_summary and conversation.scope == run.scope:
                    conversation.memory = {
                        "version": "conversation-memory-v1",
                        "scope": run.scope,
                        "summary": plan.memory_summary,
                    }
                if plan.clarification is not None:
                    run.clarification = plan.clarification
                    message.task_result = {"kind": "clarification", "text": plan.clarification}
                    message.status = "answered"
                    await self._advance(
                        session,
                        run,
                        "clarify",
                        "completed" if run.stage == "replan" else "waiting_input",
                    )
                else:
                    await self._advance(session, run, plan.steps[0], "queued")
                    if message.status == "answered":
                        message.task_result, message.status = None, "queued"
                return
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
        self,
        public_id: UUID,
        fence: UUID,
        answer: dict[str, Any],
        usage: dict[str, Any],
        coverage: dict[str, int] | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            check_run_budget(run.usage, run.graph_version)
            message = await session.get(ConversationMessage, run.message_id)
            assert message is not None
            if (
                run.graph_version in BATCHED_GRAPH_VERSIONS
                and (run.plan or {}).get("summary_mode") == "overview"
            ):
                assert coverage is not None
                batch_index = overview_batch_index(run.stage)
                if coverage["completed_batches"] != batch_index + 1:
                    raise RagFailure("TASK_PLAN_INVALID", "资料整理进度不一致")
                answer = merge_overview_answers(message.answer, answer)
                previous_usage = message.usage or {}
                usage = {
                    **usage,
                    "overview_version": OVERVIEW_VERSION,
                    **{
                        field: previous_usage.get(field, 0) + usage.get(field, 0)
                        for field in ("prompt_tokens", "completion_tokens")
                    },
                }
                has_more = coverage["completed_batches"] < coverage["total_batches"]
                budget_available = (
                    run.usage.get("model_calls", 0) < MAX_RUN_CALLS
                    and run.usage.get("prompt_tokens", 0)
                    + run.usage.get("completion_tokens", 0)
                    + 18_000
                    <= MAX_RUN_TOKENS
                    and run.usage.get("cost_units", 0) + 30_000 <= MAX_RUN_COST_UNITS
                    and run.usage.get("execution_seconds", 0) + 110
                    <= run_seconds_limit(run.graph_version)
                )
                coverage = {
                    **coverage,
                    "budget_limited": int(has_more and not budget_available),
                    "knowledge_points": len(answer["claims"]),
                    "cited_pages": len(
                        {
                            (c.get("document_id"), c["unit"])
                            for claim in answer["claims"]
                            for c in claim["citations"]
                        }
                    ),
                }
                message.answer, message.usage = answer, usage
                run.outputs = [output for output in run.outputs if output["kind"] != "summary"]
                if has_more and budget_available:
                    run.outputs = [
                        *run.outputs,
                        {
                            "kind": "summary",
                            "message_id": str(message.public_id),
                            "text": "正在分批整理资料",
                            "coverage": coverage,
                        },
                    ]
                    await self._advance(session, run, f"overview_{batch_index + 1}", "queued")
                    return
            message.answer, message.usage = answer, usage
            message.status = "insufficient" if answer["insufficient_evidence"] else "answered"
            message.fence, message.lease_until = None, None
            run.outputs = [
                *run.outputs,
                {
                    "kind": "summary",
                    "message_id": str(message.public_id),
                    "text": "资料依据不足" if answer["insufficient_evidence"] else "已完成资料总结",
                    **({"coverage": coverage} if coverage else {}),
                },
            ]
            if run.graph_version in COMPOSABLE_GRAPH_VERSIONS:
                steps = (run.plan or {}).get("steps", [])
                current = steps.index("summary")
                next_step = steps[current + 1] if current + 1 < len(steps) else "done"
                if answer["insufficient_evidence"]:
                    next_step = "done"
                await self._advance(
                    session, run, next_step, "completed" if next_step == "done" else "queued"
                )
                return
            study = (run.plan or {}).get("action") == "study"
            await self._advance(
                session,
                run,
                "quiz" if study else "done",
                "queued" if study and not answer["insufficient_evidence"] else "completed",
            )

    async def publish_pdf(self, public_id: UUID, fence: UUID) -> None:
        """Publish a downloadable rendering of the already validated, persisted answer."""
        async with self.sessions.begin() as session:
            run = await self._active(session, public_id, fence)
            if run.graph_version not in COMPOSABLE_GRAPH_VERSIONS or run.stage != "pdf":
                raise RagFailure("TASK_PLAN_INVALID", "PDF 阶段不可用")
            message = await session.get(ConversationMessage, run.message_id)
            assert message is not None
            if not message.answer or message.answer.get("insufficient_evidence"):
                raise RagFailure("ANSWER_INVALID", "没有可导出的资料总结")
            run.outputs = [
                *run.outputs,
                {
                    "kind": "pdf",
                    "message_id": str(message.public_id),
                    "export_version": PDF_EXPORT_VERSION,
                    "text": "知识点 PDF 已准备好",
                },
            ]
            steps = (run.plan or {}).get("steps", [])
            current = steps.index("pdf")
            next_step = steps[current + 1] if current + 1 < len(steps) else "done"
            await self._advance(
                session, run, next_step, "completed" if next_step == "done" else "queued"
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
            check_run_budget(run.usage, run.graph_version)
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
            if run.graph_version in COMPOSABLE_GRAPH_VERSIONS:
                steps = (run.plan or {}).get("steps", [])
                study = steps[0] != "quiz"
            else:
                study = (run.plan or {}).get("action") == "study"
            if not study:
                message.task_result, message.quiz_id, message.usage = output, quiz.id, usage
            message.status, message.fence, message.lease_until = "answered", None, None
            # A weak-topic Quiz is the final output; never recursively schedule more practice.
            wait = (
                run.stage == "quiz" and "review" in (run.plan or {}).get("steps", [])
                if run.graph_version in COMPOSABLE_GRAPH_VERSIONS
                else study
                and run.stage == "quiz"
                and (run.plan or {}).get("review_after_submit", False)
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
                run.outputs = [*run.outputs, output]
            if run.graph_version in COMPOSABLE_GRAPH_VERSIONS:
                standalone = plan.get("steps", ["review"])[0] == "review"
            else:
                standalone = plan.get("action") != "study"
            if standalone:
                message.task_result = output
                message.status, message.fence, message.lease_until = "answered", None, None
            practice = (
                "practice" in plan.get("steps", [])
                if run.graph_version in COMPOSABLE_GRAPH_VERSIONS
                else plan.get("action") == "practice_weak_topics"
                or plan.get("practice_after_review")
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
