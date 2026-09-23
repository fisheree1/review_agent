from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol
from uuid import UUID

from app.core.errors import ApplicationError
from app.learning.domain import validate_blueprint, validate_candidates
from app.learning.store import SqlLearningStore
from app.rag.domain import Evidence, RagFailure, validate_answer, validate_vectors
from app.rag.ports import Embeddings

logger = logging.getLogger(__name__)


class LearningModel(Protocol):
    async def answer(
        self, question: str, sources: list[Evidence], *, history: list[dict[str, str]] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...
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
                # Same-scope recent questions clarify short follow-ups; old scope is never sent.
                query = " ".join([history[-1]["question"], question]) if history else question
                vectors = await self.embeddings.embed([query[:3000]], query=True)
                validate_vectors(vectors, 1)
                sources = await self.store.message_sources(
                    public_id, fence, vectors[0], query[:3000]
                )
                usage: dict[str, Any]
                if not sources:
                    answer, usage = {"insufficient_evidence": True, "claims": []}, {}
                else:
                    payload, usage = await self.model.answer(question, sources, history=history)
                    answer = validate_answer(payload, sources)
                await self.store.finish_message(public_id, fence, answer, usage)
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
                await self.store.finish_quiz(public_id, fence, questions)
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
