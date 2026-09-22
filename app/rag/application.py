from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.core.errors import ApplicationError
from app.rag.domain import RagFailure, Scope, Task, split_content, validate_answer, validate_vectors
from app.rag.ports import AnswerModel, Embeddings, RagStore

logger = logging.getLogger(__name__)


class RagService:
    def __init__(self, store: RagStore) -> None:
        self.store = store

    async def index(self, scope: Scope, key: str | None = None) -> dict[str, Any]:
        return await self.store.index(scope, key)

    async def ask(self, scope: Scope, key: str, question: str) -> dict[str, Any]:
        question = question.strip()
        if not 2 <= len(question) <= 2000:
            raise ApplicationError(
                code="QUESTION_INVALID", message="问题需为 2–2000 字", status_code=422
            )
        return await self.store.ask(scope, key, question)

    async def question(self, scope: Scope, question_id: UUID) -> dict[str, Any]:
        return await self.store.question(scope, question_id)

    async def history(self, scope: Scope) -> list[dict[str, Any]]:
        return await self.store.history(scope)

    async def cancel(self, scope: Scope, question_id: UUID) -> dict[str, Any]:
        return await self.store.cancel(scope, question_id)


class RagProcessor:
    def __init__(self, store: RagStore, embeddings: Embeddings, model: AnswerModel) -> None:
        self.store = store
        self.embeddings = embeddings
        self.model = model

    async def process_index(self) -> bool:
        task = await self.store.claim_index()
        if task is None:
            return False
        try:
            units = await self.store.units(task)
            if units is not None and not await self.store.prepare(task, split_content(units)):
                return True
            # One batch per claim gives reading questions a turn and checkpoints billed work.
            sources = await self.store.pending(task)
            if sources:
                async with asyncio.timeout(100):
                    vectors = await self.embeddings.embed([source.content for source in sources])
                validate_vectors(vectors, len(sources))
                if not await self.store.save_vectors(task, sources, vectors):
                    return True
            await self.store.finish_index(task)
        except RagFailure as exc:
            await self.store.fail_index(task, exc.code)
        except TimeoutError:
            await self.store.fail_index(task, "PROVIDER_TIMEOUT")
        except Exception as exc:
            # Provider and SQL exceptions can include private text; log type and stable ID only.
            logger.error("rag_index_failed id=%s error_type=%s", task.id, type(exc).__name__)
            await self.store.fail_index(task, "INDEX_FAILED")
        return True

    async def process_question(self) -> bool:
        task = await self.store.claim_question()
        if task is None:
            return False
        try:
            async with asyncio.timeout(110):
                await self._answer(task)
        except RagFailure as exc:
            await self.store.fail_question(task, exc.code)
        except TimeoutError:
            await self.store.fail_question(task, "PROVIDER_TIMEOUT")
        except Exception as exc:
            logger.error("rag_question_failed id=%s error_type=%s", task.id, type(exc).__name__)
            await self.store.fail_question(task, "ANSWER_FAILED")
        return True

    async def _answer(self, task: Task) -> None:
        if not await self.store.active(task):
            return
        vectors = await self.embeddings.embed([task.question], query=True)
        validate_vectors(vectors, 1)
        sources = await self.store.retrieve(task, vectors[0])
        if not sources:
            await self.store.finish_question(
                task, {"insufficient_evidence": True, "claims": []}, {}
            )
            return
        if not await self.store.active(task):
            return
        answer, usage = await self.model.answer(task.question, sources)
        await self.store.finish_question(task, validate_answer(answer, sources), usage)
