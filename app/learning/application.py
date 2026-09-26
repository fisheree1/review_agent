from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol
from uuid import UUID

from app.core.errors import ApplicationError
from app.learning.agent import AgentBudget, PlanningModel, run_scoped_agent
from app.learning.conversation_tasks import TASK_VERSION, TaskPlanningModel, validate_task_plan
from app.learning.domain import validate_blueprint, validate_candidates
from app.learning.quiz_agent import QuizPlanningModel, run_quiz_agent
from app.learning.store import SqlLearningStore
from app.rag.domain import Evidence, RagFailure, validate_vectors
from app.rag.ports import Embeddings

logger = logging.getLogger(__name__)


class LearningModel(PlanningModel, QuizPlanningModel, TaskPlanningModel, Protocol):
    async def generate_quiz(
        self, config: dict[str, Any], sources: list[Evidence]
    ) -> dict[str, Any]: ...
    async def grade_short(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


class LearningService:
    def __init__(self, store: SqlLearningStore) -> None:
        self.store = store

    async def create_collection(
        self, workspace: UUID, name: str, description: str
    ) -> dict[str, Any]:
        name = name.strip()
        if not 1 <= len(name) <= 120:
            raise ApplicationError(
                code="COLLECTION_INVALID", message="集合名称需为 1–120 字", status_code=422
            )
        return await self.store.create_collection(workspace, name, description.strip())

    async def create_conversation(
        self, workspace: UUID, title: str, documents: list[UUID], collections: list[UUID]
    ) -> dict[str, Any]:
        title = title.strip()
        if not 1 <= len(title) <= 160:
            raise ApplicationError(
                code="CONVERSATION_INVALID", message="请填写对话标题", status_code=422
            )
        return await self.store.create_conversation(workspace, title, documents, collections)

    async def ask(
        self, workspace: UUID, conversation_id: UUID, key: str, question: str
    ) -> dict[str, Any]:
        question = question.strip()
        if not 2 <= len(question) <= 2000:
            raise ApplicationError(
                code="QUESTION_INVALID", message="问题需为 2–2000 字", status_code=422
            )
        return await self.store.ask(workspace, conversation_id, key, question)

    async def create_quiz(
        self,
        workspace: UUID,
        key: str,
        title: str,
        config: dict[str, Any],
        documents: list[UUID],
        collections: list[UUID],
    ) -> dict[str, Any]:
        title = title.strip()
        if not 1 <= len(title) <= 160:
            raise ApplicationError(code="QUIZ_INVALID", message="请填写 Quiz 标题", status_code=422)
        try:
            blueprint = validate_blueprint(config)
        except ValueError as exc:
            raise ApplicationError(
                code="QUIZ_INVALID", message="出题设置无效", status_code=422
            ) from exc
        return await self.store.create_quiz(
            workspace, key, title, blueprint, documents, collections
        )


class LearningProcessor:
    def __init__(
        self, store: SqlLearningStore, embeddings: Embeddings, model: LearningModel
    ) -> None:
        self.store = store
        self.embeddings = embeddings
        self.model = model

    async def process_message(self) -> bool:
        task = await self.store.claim_message()
        if task is None:
            return False
        public_id, fence, _workspace, question, _scope, history = task
        try:
            async with asyncio.timeout(110):

                async def search(vector: list[float], query: str) -> list[Evidence]:
                    return await self.store.message_sources(public_id, fence, vector, query)

                async def ensure_active() -> None:
                    await self.store.ensure_message_active(public_id, fence)

                await ensure_active()
                review = await self.store.message_review_context(public_id, fence)
                budget = AgentBudget()
                payload, routing_usage = await self.model.plan_task(
                    question,
                    history=history,
                    review_available=review is not None,
                )
                budget.charge(routing_usage)
                plan = validate_task_plan(payload)
                await ensure_active()

                def task_usage(usage: dict[str, Any]) -> dict[str, Any]:
                    return {
                        **usage,
                        "task_version": TASK_VERSION,
                        "task_action": plan.action,
                        "task_prompt_version": routing_usage.get("prompt_version"),
                    }

                if plan.action == "answer":
                    answer_history = history
                    if review and review["summary"]:
                        answer_history = [
                            *history[-2:],
                            {"question": "最近提交练习的错题", "answer": review["summary"]},
                        ]
                    answer, usage = await run_scoped_agent(
                        question,
                        answer_history,
                        embeddings=self.embeddings,
                        model=self.model,
                        search=search,
                        ensure_active=ensure_active,
                        budget=budget,
                    )
                    await self.store.finish_message(public_id, fence, answer, task_usage(usage))
                elif plan.action in ("create_quiz", "practice_weak_topics") and (
                    plan.action == "create_quiz" or (review and review["weak_topics"])
                ):
                    config = plan.quiz_config(review["weak_topics"] if review else None)
                    title = plan.title or "学习练习"
                    await self.store.prepare_message_quiz(public_id, fence, title)
                    questions, usage = await run_quiz_agent(
                        config,
                        embeddings=self.embeddings,
                        model=self.model,
                        search=search,
                        ensure_active=ensure_active,
                        budget=budget,
                    )
                    await self.store.finish_message_quiz(
                        public_id,
                        fence,
                        title,
                        config,
                        questions,
                        task_usage(usage),
                    )
                else:
                    if plan.action == "review_mistakes" and review:
                        result = {
                            "kind": "review",
                            "quiz_id": review["quiz_id"],
                            "attempt_id": review["attempt_id"],
                            "title": review["title"],
                            "text": "以下是当前资料范围最近一次练习的作答、解析与来源。",
                        }
                    else:
                        text = (
                            plan.message
                            if plan.action == "clarify"
                            else (
                                "当前资料范围还没有已完成的练习，请先要求生成 Quiz 并提交作答。"
                                if review is None
                                else "最近一次练习没有明显薄弱点，可以要求生成新练习。"
                            )
                        )
                        result = {
                            "kind": "clarification",
                            "text": text or "请说明希望完成的学习任务。",
                        }
                    usage = budget.usage(
                        routing_usage,
                        steps=1,
                        searches=0,
                        reads=0,
                        planning_prompt_version=routing_usage.get("prompt_version"),
                        trace=[{"tool": "plan_task", "action": plan.action}],
                    )
                    await self.store.finish_message_task(
                        public_id, fence, result, task_usage(usage)
                    )
        except RagFailure as exc:
            await self.store.fail_message(public_id, fence, exc.code)
        except TimeoutError:
            await self.store.fail_message(public_id, fence, "PROVIDER_TIMEOUT")
        except Exception as exc:
            logger.error(
                "conversation_message_failed id=%s error_type=%s", public_id, type(exc).__name__
            )
            await self.store.fail_message(public_id, fence, "ANSWER_FAILED")
        return True

    async def process_quiz(self) -> bool:
        task = await self.store.claim_quiz()
        if task is None:
            return False
        public_id, fence, _workspace, config, _scope = task
        try:
            async with asyncio.timeout(110):
                usage = None
                if config.get("generation_mode") == "agent":

                    async def search(vector: list[float], query: str) -> list[Evidence]:
                        return await self.store.quiz_evidence(public_id, fence, vector, query)

                    async def ensure_active() -> None:
                        await self.store.ensure_quiz_active(public_id, fence)

                    questions, usage = await run_quiz_agent(
                        config,
                        embeddings=self.embeddings,
                        model=self.model,
                        search=search,
                        ensure_active=ensure_active,
                    )
                else:
                    topic = config.get("topic", "")
                    vector = None
                    if topic:
                        vectors = await self.embeddings.embed([topic], query=True)
                        validate_vectors(vectors, 1)
                        vector = vectors[0]
                    sources = await self.store.quiz_evidence(public_id, fence, vector, topic)
                    if not sources:
                        raise RagFailure("QUIZ_NO_EVIDENCE", "没有可用证据")
                    payload = await self.model.generate_quiz(config, sources)
                    questions = validate_candidates(payload, config, sources)
                    if not questions:
                        raise RagFailure("QUIZ_INVALID", "没有有效题目")
                await self.store.finish_quiz(public_id, fence, questions, usage)
        except RagFailure as exc:
            await self.store.fail_quiz(public_id, fence, exc.code)
        except TimeoutError:
            await self.store.fail_quiz(public_id, fence, "PROVIDER_TIMEOUT")
        except Exception as exc:
            logger.error(
                "quiz_generation_failed id=%s error_type=%s", public_id, type(exc).__name__
            )
            await self.store.fail_quiz(public_id, fence, "QUIZ_FAILED")
        return True

    async def process_grading(self) -> bool:
        task = await self.store.claim_grading()
        if task is None:
            return False
        public_id, fence, items = task
        try:
            async with asyncio.timeout(110):
                grades = await self.model.grade_short(items)
                await self.store.finish_grading(public_id, fence, grades)
        except RagFailure as exc:
            await self.store.fail_grading(public_id, fence, exc.code)
        except TimeoutError:
            await self.store.fail_grading(public_id, fence, "PROVIDER_TIMEOUT")
        except Exception as exc:
            logger.error("quiz_grading_failed id=%s error_type=%s", public_id, type(exc).__name__)
            await self.store.fail_grading(public_id, fence, "GRADING_FAILED")
        return True
