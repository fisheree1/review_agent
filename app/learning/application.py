from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol
from uuid import UUID

from app.core.errors import ApplicationError
from app.learning.agent import StudyGenerationModel
from app.learning.conversation_tasks import TaskPlanningModel
from app.learning.domain import validate_blueprint
from app.learning.quiz_generation import QuizGenerationModel, generate_scoped_quiz
from app.learning.store import SqlLearningStore
from app.rag.domain import Evidence, RagFailure
from app.rag.ports import Embeddings

logger = logging.getLogger(__name__)


class LearningModel(StudyGenerationModel, QuizGenerationModel, TaskPlanningModel, Protocol):
    async def grade_short(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    async def grade_short_with_usage(
        self, items: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...


class LearningService:
    def __init__(self, store: SqlLearningStore) -> None:
        self.store = store

    async def create_collection(
        self, workspace: UUID, name: str, description: str, document_ids: list[UUID] | None = None
    ) -> dict[str, Any]:
        name = name.strip()
        if not 1 <= len(name) <= 120:
            raise ApplicationError(
                code="COLLECTION_INVALID", message="集合名称需为 1–120 字", status_code=422
            )
        documents = document_ids or []
        if len(documents) > 100 or len(set(documents)) != len(documents):
            raise ApplicationError(
                code="SCOPE_INVALID", message="集合最多包含 100 份不重复资料", status_code=422
            )
        return await self.store.create_collection(workspace, name, description.strip(), documents)

    async def create_conversation(
        self,
        workspace: UUID,
        title: str,
        documents: list[UUID],
        collections: list[UUID],
        key: str | None = None,
    ) -> dict[str, Any]:
        title = title.strip()
        if not 1 <= len(title) <= 160:
            raise ApplicationError(
                code="CONVERSATION_INVALID", message="请填写对话标题", status_code=422
            )
        return await self.store.create_conversation(workspace, title, documents, collections, key)

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

    async def process_quiz(self) -> bool:
        task = await self.store.claim_quiz()
        if task is None:
            return False
        public_id, fence, _workspace, config, _scope = task
        try:
            async with asyncio.timeout(110):

                async def sources(vector: list[float] | None, topic: str) -> list[Evidence]:
                    return await self.store.quiz_evidence(public_id, fence, vector, topic)

                async def ensure_active() -> None:
                    await self.store.ensure_quiz_active(public_id, fence)

                # Standard and agent modes share one evidence selection and one generation.
                questions, usage = await generate_scoped_quiz(
                    config,
                    embeddings=self.embeddings,
                    model=self.model,
                    sources=sources,
                    ensure_active=ensure_active,
                    charge_usage=config.get("generation_mode") == "agent",
                )
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
