"""Recoverable learning stages using typed executor and persisted call receipts."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from app.core.errors import ApplicationError
from app.learning.agent import AgentBudget
from app.learning.application import LearningModel
from app.learning.conversation_tasks import TASK_VERSION, validate_task_plan
from app.learning.focused_answer import answer_focused
from app.learning.graph_tasks import GRAPH_TASK_VERSION, validate_graph_task_plan
from app.learning.overview import OVERVIEW_VERSION, overview_batch_index
from app.learning.study_generation import generate_study
from app.learning.workflow import (
    BATCHED_GRAPH_VERSIONS,
    COMPOSABLE_GRAPH_VERSIONS,
    FAST_GRAPH_VERSIONS,
    GRAPH_VERSION,
    AgentRunPersistence,
    StudyExecutor,
)
from app.rag.domain import STUDY_PROMPT_VERSION, Evidence, RagFailure, validate_study_answer
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

    async def plan_step(
        self, question: str, *, history: list[dict[str, str]], observations: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = await self.calls.invoke(
            "plan_step",
            lambda: self.models.plan_step(question, history=history, observations=observations),
        )
        return result[0], result[1]

    async def plan_quiz_step(
        self, config: dict[str, Any], *, observations: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = await self.calls.invoke(
            "plan_quiz_step", lambda: self.models.plan_quiz_step(config, observations=observations)
        )
        return result[0], result[1]

    async def answer(
        self, question: str, sources: list[Evidence], *, history: list[dict[str, str]] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = await self.calls.invoke(
            "answer", lambda: self.models.answer(question, sources, history=history)
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


class AgentRunProcessor:
    def __init__(
        self,
        store: AgentRunPersistence,
        embeddings: Embeddings,
        model: LearningModel,
        executor: StudyExecutor,
    ) -> None:
        self.store, self.embeddings, self.model, self.executor = store, embeddings, model, executor

    async def process_next(self) -> bool:
        task = await self.store.claim()
        if task is None:
            return False
        run_id, fence = UUID(task["id"]), task["fence"]
        started = time.monotonic()
        calls = RecordedCalls(self.store, run_id, fence)
        models = RecordedModels(self.model, calls)
        embeddings = RecordedEmbeddings(self.embeddings, calls)

        async def ensure_active() -> None:
            await self.store.ensure_active(run_id, fence)

        async def search(vector: list[float], query: str) -> list[Evidence]:
            return await self.store.sources(run_id, fence, vector, query)

        async def record_validation(attempt: int, diagnosis: dict[str, Any]) -> None:
            await self.store.record_answer_validation(run_id, fence, attempt, diagnosis)

        try:
            async with asyncio.timeout(110):
                stage = task["stage"]
                if stage in ("plan", "replan"):
                    request = task["request"]
                    if task["response"]:
                        request += "\n用户补充：" + task["response"]
                    review = await self.store.review(run_id, fence)
                    if task["graph_version"] in COMPOSABLE_GRAPH_VERSIONS:
                        payload, _usage = await models.plan_graph_task(
                            request,
                            history=task["history"],
                            review_available=review is not None,
                            memory_summary=task["memory_summary"],
                        )
                        await self.store.save_plan(run_id, fence, validate_graph_task_plan(payload))
                    else:
                        payload, _usage = await models.plan_task(
                            request, history=task["history"], review_available=review is not None
                        )
                        await self.store.save_plan(run_id, fence, validate_task_plan(payload))
                elif stage == "summary" or (
                    task["graph_version"] in BATCHED_GRAPH_VERSIONS
                    and stage.startswith("overview_")
                ):
                    question = (task["plan"] or {}).get("summary_request") or task["request"]
                    if (
                        task["graph_version"] in BATCHED_GRAPH_VERSIONS
                        and (task["plan"] or {}).get("summary_mode") == "overview"
                    ):
                        batch = overview_batch_index(stage)
                        sources, coverage = await self.store.overview_batch(run_id, fence)
                        if sources and task["graph_version"] == GRAPH_VERSION:
                            result, usage = await generate_study(
                                f"{question}\n整理当前第 {batch + 1} 批资料中的独立知识点，"
                                "兼顾本批各页的定义、方法、条件和例子；不要声称覆盖未提供页面。",
                                sources,
                                task["history"],
                                model=models,
                                budget=AgentBudget(),
                                ensure_active=ensure_active,
                                mode="overview",
                                record_validation=record_validation,
                            )
                        elif sources:
                            await ensure_active()
                            raw, usage = await models.answer_study(
                                f"{question}\n整理当前第 {batch + 1} 批资料中的独立知识点，"
                                "兼顾本批各页的定义、方法、条件和例子；不要声称覆盖未提供页面。",
                                sources,
                                history=task["history"],
                                mode="overview",
                            )
                            AgentBudget().charge(usage)
                            result = validate_study_answer(
                                raw,
                                sources,
                                require_sections=usage.get("prompt_version")
                                == STUDY_PROMPT_VERSION,
                            )
                        else:
                            result, usage = {"insufficient_evidence": True, "claims": []}, {}
                        await ensure_active()
                        await self.store.publish_summary(
                            run_id,
                            fence,
                            result,
                            {**usage, "overview_version": OVERVIEW_VERSION},
                            coverage,
                        )
                    elif (
                        task["graph_version"] in COMPOSABLE_GRAPH_VERSIONS
                        and (task["plan"] or {}).get("summary_mode") == "overview"
                    ):
                        sources, coverage = await self.store.overview_sources(run_id, fence)
                        if sources:
                            raw, usage = await models.answer_study(
                                question, sources, history=task["history"]
                            )
                            result = validate_study_answer(
                                raw,
                                sources,
                                require_sections=usage.get("prompt_version")
                                == STUDY_PROMPT_VERSION,
                            )
                        else:
                            result, usage = {"insufficient_evidence": True, "claims": []}, {}
                        await ensure_active()
                        await self.store.publish_summary(run_id, fence, result, usage, coverage)
                    elif task["graph_version"] in FAST_GRAPH_VERSIONS:
                        result, usage = await answer_focused(
                            question,
                            task["history"],
                            embeddings=embeddings,
                            model=models,
                            search=search,
                            ensure_active=ensure_active,
                            reliable=task["graph_version"] == GRAPH_VERSION,
                            record_validation=record_validation,
                        )
                        usage["graph_version"] = task["graph_version"]
                        await self.store.publish_summary(run_id, fence, result, usage)
                    else:
                        result, usage = await self.executor.answer(
                            question,
                            task["history"],
                            embeddings=embeddings,
                            model=models,
                            search=search,
                            ensure_active=ensure_active,
                            budget=AgentBudget(),
                        )
                        usage["graph_version"] = task["graph_version"]
                        await self.store.publish_summary(run_id, fence, result, usage)
                elif stage == "pdf":
                    await self.store.publish_pdf(run_id, fence)
                elif stage in ("quiz", "weak"):
                    if task["graph_version"] in COMPOSABLE_GRAPH_VERSIONS:
                        plan = validate_graph_task_plan(
                            {k: v for k, v in task["plan"].items() if k != "weak_topics"}
                        )
                        config = plan.quiz_config(
                            task["plan"].get("weak_topics") if stage == "weak" else None
                        )
                    else:
                        legacy_plan = validate_task_plan(
                            {k: v for k, v in task["plan"].items() if k != "weak_topics"}
                        )
                        config = legacy_plan.quiz_config(task["plan"].get("weak_topics"))
                    if stage == "weak":
                        config["topic"] = "、".join(task["plan"]["weak_topics"])[:120]
                    await self.store.reserve_quiz(run_id, fence)
                    questions, usage = await self.executor.quiz(
                        config,
                        embeddings=embeddings,
                        model=models,
                        search=search,
                        ensure_active=ensure_active,
                        budget=AgentBudget(),
                    )
                    usage["graph_version"] = task["graph_version"]
                    await self.store.publish_quiz(run_id, fence, config, questions, usage)
                elif stage == "review":
                    review = await self.store.review(run_id, fence)
                    await self.store.publish_review(run_id, fence, review)
                elif stage == "grade":
                    attempt, items = await self.store.grading_items(run_id, fence)
                    grades = await models.grade(items)
                    await ensure_active()
                    await self.store.finish_grading(attempt, fence, grades)
                else:
                    raise RagFailure("RUN_VERSION_UNSUPPORTED", "运行阶段不可识别")
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
            await self.store.record_duration(run_id, time.monotonic() - started)
        return True
