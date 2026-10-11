"""Recoverable learning stages using deterministic evidence and persisted call receipts."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.core.errors import ApplicationError
from app.learning.agent import AgentBudget, RunCheck, SourceSearch
from app.learning.application import LearningModel
from app.learning.conversation_tasks import TASK_VERSION, validate_task_plan
from app.learning.focused_answer import answer_focused
from app.learning.graph_tasks import GRAPH_TASK_VERSION, validate_graph_task_plan
from app.learning.overview import OVERVIEW_VERSION, overview_batch_index
from app.learning.quiz_generation import QuizSources, generate_scoped_quiz
from app.learning.run_flow import (
    GRADE,
    OVERVIEW_PREFIX,
    PDF,
    PLAN,
    QUIZ,
    REPLAN,
    REVIEW,
    STAGE_TIMEOUT_SECONDS,
    SUMMARY,
    WEAK,
)
from app.learning.study_generation import ValidationRecorder, generate_study
from app.learning.workflow import SUPPORTED_GRAPH_VERSIONS, AgentRunPersistence, is_composable
from app.rag.domain import Evidence, RagFailure
from app.rag.ports import Embeddings

logger = logging.getLogger(__name__)


class RecordedCalls:
    """Ordinal call receipts permit replay of returned results, never unknown requests."""

    def __init__(self, store: AgentRunPersistence, run_id: UUID, fence: UUID) -> None:
        self.store, self.run_id, self.fence = store, run_id, fence
        self.ordinal = 0

    async def invoke(self, kind: str, invoke: Callable[[], Awaitable[Any]]) -> Any:
        ordinal = self.ordinal
        self.ordinal += 1
        saved = await self.store.begin_call(self.run_id, self.fence, ordinal, kind)
        if saved is not None:
            return saved
        try:
            result = await invoke()
        except RagFailure as exc:
            if exc.code in {
                "PROVIDER_UNCONFIGURED",
                "PROVIDER_AUTH",
                "PROVIDER_QUOTA",
                "PROVIDER_LIMIT",
                "PROVIDER_RESPONSE",
                "EMBEDDING_INVALID",
                "ANSWER_INVALID",
                "AGENT_PLAN_INVALID",
                "AGENT_CONTEXT_LIMIT",
                "QUIZ_INVALID",
                "GRADING_INVALID",
            }:
                await self.store.reject_call(self.run_id, self.fence, ordinal)
            raise
        usage: dict[str, Any]
        if kind == "embed":
            usage = {"embedding_calls": 1}
        else:
            prompt, completion = result[1].get("prompt_tokens"), result[1].get("completion_tokens")
            if (
                type(prompt) is not int
                or type(completion) is not int
                or prompt < 1
                or completion < 1
            ):
                await self.store.reject_call(self.run_id, self.fence, ordinal)
                raise RagFailure("AGENT_USAGE_INVALID", "模型未返回可靠的计费信息")
            # Persist a received result and actual charges before stage budget validation.
            usage = {
                "model_calls": 1,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cost_units": prompt + 4 * completion,
                "model": result[1].get("model"),
                "prompt_version": result[1].get("prompt_version"),
                "task_schema_version": (
                    GRAPH_TASK_VERSION
                    if kind == "plan_graph_task"
                    else TASK_VERSION
                    if kind == "plan_task"
                    else None
                ),
                **(
                    {"generation_error": result[1]["generation_error"]}
                    if result[1].get("generation_error")
                    in {"truncated", "invalid_json", "invalid_shape"}
                    else {}
                ),
            }
        try:
            await self.store.finish_call(self.run_id, self.fence, ordinal, result, usage)
        except RagFailure as exc:
            if exc.code == "AGENT_CONTEXT_LIMIT":
                await self.store.reject_call(self.run_id, self.fence, ordinal)
            raise
        return result


class RecordedModels:
    def __init__(self, models: LearningModel, calls: RecordedCalls) -> None:
        self.models, self.calls = models, calls

    async def plan_task(
        self, request: str, *, history: list[dict[str, str]], review_available: bool
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = await self.calls.invoke(
            "plan_task",
            lambda: self.models.plan_task(
                request, history=history, review_available=review_available
            ),
        )
        return result[0], result[1]

    async def plan_graph_task(
        self,
        request: str,
        *,
        history: list[dict[str, str]],
        review_available: bool,
        memory_summary: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = await self.calls.invoke(
            "plan_graph_task",
            lambda: self.models.plan_graph_task(
                request,
                history=history,
                review_available=review_available,
                memory_summary=memory_summary,
            ),
        )
        return result[0], result[1]

    async def answer_study(
        self,
        question: str,
        sources: list[Evidence],
        *,
        history: list[dict[str, str]] | None = None,
        mode: Literal["focused", "overview"] | None = None,
        repair_feedback: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        async def invoke() -> tuple[dict[str, Any], dict[str, Any]]:
            if mode is None:
                return await self.models.answer_study(question, sources, history=history)
            return await self.models.answer_study(
                question, sources, history=history, mode=mode, repair_feedback=repair_feedback
            )

        result = await self.calls.invoke("answer", invoke)
        return result[0], result[1]

    async def generate_quiz_with_usage(
        self, config: dict[str, Any], sources: list[Evidence]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = await self.calls.invoke(
            "generate_quiz", lambda: self.models.generate_quiz_with_usage(config, sources)
        )
        return result[0], result[1]

    async def grade(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = await self.calls.invoke("grade", lambda: self.models.grade_short_with_usage(items))
        return list(result[0])


class RecordedEmbeddings:
    def __init__(self, embeddings: Embeddings, calls: RecordedCalls) -> None:
        self.embeddings, self.calls = embeddings, calls

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        result = await self.calls.invoke("embed", lambda: self.embeddings.embed(texts, query=query))
        return list(result)


@dataclass(frozen=True, slots=True)
class StageContext:
    """Everything one claimed stage may use; every external call goes through receipts."""

    task: dict[str, Any]
    run_id: UUID
    fence: UUID
    models: RecordedModels
    embeddings: RecordedEmbeddings
    ensure_active: RunCheck
    search: SourceSearch
    quiz_sources: QuizSources
    record_validation: ValidationRecorder

    @property
    def composable(self) -> bool:
        return is_composable(self.task["graph_version"])

    @property
    def plan(self) -> dict[str, Any]:
        return self.task["plan"] or {}


StageHandler = Callable[[StageContext], Awaitable[None]]


class AgentRunProcessor:
    def __init__(
        self,
        store: AgentRunPersistence,
        embeddings: Embeddings,
        model: LearningModel,
    ) -> None:
        self.store, self.embeddings, self.model = store, embeddings, model

    def _handler(self, stage: str, *, composable: bool) -> StageHandler | None:
        if stage in (PLAN, REPLAN):
            return self._plan
        if stage == SUMMARY or (composable and stage.startswith(OVERVIEW_PREFIX)):
            return self._summary
        handlers: dict[str, StageHandler] = {
            QUIZ: self._quiz,
            WEAK: self._quiz,
            REVIEW: self._review,
            GRADE: self._grade,
        }
        if composable:
            handlers[PDF] = self._pdf
        return handlers.get(stage)

    async def process_next(self) -> bool:
        task = await self.store.claim()
        if task is None:
            return False
        run_id, fence = UUID(task["id"]), task["fence"]
        started = time.monotonic()
        calls = RecordedCalls(self.store, run_id, fence)

        async def ensure_active() -> None:
            await self.store.ensure_active(run_id, fence)

        async def search(vector: list[float], query: str) -> list[Evidence]:
            return await self.store.sources(run_id, fence, vector, query)

        async def quiz_sources(vector: list[float] | None, topic: str) -> list[Evidence]:
            return await self.store.quiz_sources(run_id, fence, vector, topic)

        async def record_validation(attempt: int, diagnosis: dict[str, Any]) -> None:
            await self.store.record_answer_validation(run_id, fence, attempt, diagnosis)

        context = StageContext(
            task=task,
            run_id=run_id,
            fence=fence,
            models=RecordedModels(self.model, calls),
            embeddings=RecordedEmbeddings(self.embeddings, calls),
            ensure_active=ensure_active,
            search=search,
            quiz_sources=quiz_sources,
            record_validation=record_validation,
        )
        try:
            handler = (
                self._handler(task["stage"], composable=context.composable)
                if task["graph_version"] in SUPPORTED_GRAPH_VERSIONS
                else None
            )
            if handler is None:
                raise RagFailure("RUN_VERSION_UNSUPPORTED", "运行阶段不可识别")
            async with asyncio.timeout(STAGE_TIMEOUT_SECONDS):
                await handler(context)
        except RagFailure as exc:
            await self.store.fail(run_id, fence, exc.code)
        except ApplicationError as exc:
            await self.store.fail(run_id, fence, exc.code)
        except TimeoutError:
            await self.store.fail(run_id, fence, "PROVIDER_TIMEOUT")
        except Exception as exc:
            logger.error("agent_stage_failed id=%s error_type=%s", run_id, type(exc).__name__)
            await self.store.fail(run_id, fence, "ANSWER_FAILED")
        finally:
            await self.store.record_duration(
                run_id,
                time.monotonic() - started,
                task.get("reserved_seconds", STAGE_TIMEOUT_SECONDS),
            )
        return True

    async def _plan(self, ctx: StageContext) -> None:
        task = ctx.task
        request = task["request"]
        if task["response"]:
            request += "\n用户补充：" + task["response"]
        review = await self.store.review(ctx.run_id, ctx.fence)
        if ctx.composable:
            payload, _ = await ctx.models.plan_graph_task(
                request,
                history=task["history"],
                review_available=review is not None,
                memory_summary=task["memory_summary"],
            )
            await self.store.save_plan(ctx.run_id, ctx.fence, validate_graph_task_plan(payload))
            return
        payload, _ = await ctx.models.plan_task(
            request, history=task["history"], review_available=review is not None
        )
        await self.store.save_plan(ctx.run_id, ctx.fence, validate_task_plan(payload))

    async def _summary(self, ctx: StageContext) -> None:
        question = ctx.plan.get("summary_request") or ctx.task["request"]
        if ctx.composable and ctx.plan.get("summary_mode") == "overview":
            await self._overview_batch(ctx, question)
            return
        # Every focused summary uses one scoped retrieval and one generation.
        result, usage = await answer_focused(
            question,
            ctx.task["history"],
            embeddings=ctx.embeddings,
            model=ctx.models,
            search=ctx.search,
            ensure_active=ctx.ensure_active,
            reliable=ctx.composable,
            record_validation=ctx.record_validation,
        )
        usage["graph_version"] = ctx.task["graph_version"]
        await self.store.publish_summary(ctx.run_id, ctx.fence, result, usage)

    async def _overview_batch(self, ctx: StageContext, question: str) -> None:
        batch = overview_batch_index(ctx.task["stage"])
        sources, coverage = await self.store.overview_batch(ctx.run_id, ctx.fence)
        if sources:
            result, usage = await generate_study(
                f"{question}\n整理当前第 {batch + 1} 批资料中的独立知识点，"
                "兼顾本批各页的定义、方法、条件和例子；不要声称覆盖未提供页面。",
                sources,
                ctx.task["history"],
                model=ctx.models,
                budget=AgentBudget(),
                ensure_active=ctx.ensure_active,
                mode="overview",
                record_validation=ctx.record_validation,
            )
        else:
            result, usage = {"insufficient_evidence": True, "claims": []}, {}
        await ctx.ensure_active()
        await self.store.publish_summary(
            ctx.run_id,
            ctx.fence,
            result,
            {**usage, "overview_version": OVERVIEW_VERSION},
            coverage,
        )

    async def _pdf(self, ctx: StageContext) -> None:
        await self.store.publish_pdf(ctx.run_id, ctx.fence)

    async def _quiz(self, ctx: StageContext) -> None:
        stage, plan = ctx.task["stage"], ctx.plan
        weak_topics = plan.get("weak_topics")
        task_plan = {key: value for key, value in plan.items() if key != "weak_topics"}
        if ctx.composable:
            config = validate_graph_task_plan(task_plan).quiz_config(
                weak_topics if stage == WEAK else None
            )
        else:
            config = validate_task_plan(task_plan).quiz_config(weak_topics)
        if stage == WEAK:
            config["topic"] = "、".join(plan["weak_topics"])[:120]
        await self.store.reserve_quiz(ctx.run_id, ctx.fence)
        questions, usage = await generate_scoped_quiz(
            config,
            embeddings=ctx.embeddings,
            model=ctx.models,
            sources=ctx.quiz_sources,
            ensure_active=ctx.ensure_active,
        )
        usage["graph_version"] = ctx.task["graph_version"]
        await self.store.publish_quiz(ctx.run_id, ctx.fence, config, questions, usage)

    async def _review(self, ctx: StageContext) -> None:
        review = await self.store.review(ctx.run_id, ctx.fence)
        await self.store.publish_review(ctx.run_id, ctx.fence, review)

    async def _grade(self, ctx: StageContext) -> None:
        attempt, items = await self.store.grading_items(ctx.run_id, ctx.fence)
        grades = await ctx.models.grade(items)
        await ctx.ensure_active()
        await self.store.finish_grading(attempt, ctx.fence, grades)
