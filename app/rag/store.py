from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApplicationError
from app.documents.infrastructure.models import DocumentModel, DocumentPageModel, WorkspaceModel
from app.rag.domain import RETRIEVAL_VERSION, Chunk, Evidence, Scope, Task
from app.rag.messages import FAILURES
from app.rag.models import DocumentChunk, DocumentIndex, RagQuestion

LEASE_SECONDS = 180


def not_found() -> ApplicationError:
    return ApplicationError(code="DOCUMENT_NOT_FOUND", message="资料或问题不存在", status_code=404)


def question_view(question: RagQuestion) -> dict[str, Any]:
    return {
        "id": str(question.public_id),
        "question": question.question,
        "status": question.status,
        "answer": question.answer,
        "version": question.document_version_id,
        "failure_code": question.failure_code,
        "failure_message": FAILURES.get(question.failure_code or ""),
    }


class SqlRagStore:
    """Each public command is one short transaction; never encloses provider I/O."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], profile: str) -> None:
        self.sessions = sessions
        self.profile = profile

    async def _document(
        self, session: AsyncSession, scope: Scope, *, lock: bool = False
    ) -> DocumentModel:
        query = (
            select(DocumentModel)
            .join(WorkspaceModel)
            .where(
                WorkspaceModel.public_id == scope.workspace,
                WorkspaceModel.status == "active",
                DocumentModel.public_id == scope.document,
                DocumentModel.deleted_at.is_(None),
                DocumentModel.status.not_in(["deleting", "deleted"]),
            )
        )
        if lock:
            query = query.with_for_update(of=DocumentModel)
        document = await session.scalar(query)
        if document is None:
            raise not_found()
        if document.status != "ready" or document.active_version_id is None:
            raise ApplicationError(
                code="DOCUMENT_NOT_READY", message="资料仍在处理中", status_code=409
            )
        return document

    async def _index_view(
        self, session: AsyncSession, index: DocumentIndex | None
    ) -> dict[str, Any]:
        if index is None:
            return {
                "status": "not_indexed",
                "completed": 0,
                "total": 0,
                "failure_code": None,
                "failure_message": None,
            }
        total, completed = (
            await session.execute(
                select(func.count(DocumentChunk.id), func.count(DocumentChunk.embedding)).where(
                    DocumentChunk.index_id == index.id,
                    DocumentChunk.workspace_id == index.workspace_id,
                )
            )
        ).one()
        return {
            "status": index.status,
            "completed": completed,
            "total": total,
            "failure_code": index.failure_code,
            "failure_message": FAILURES.get(index.failure_code or ""),
        }

    async def index(self, scope: Scope, retry_key: str | None = None) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            document = await self._document(session, scope, lock=retry_key is not None)
            index = await session.scalar(
                select(DocumentIndex).where(
                    DocumentIndex.document_version_id == document.active_version_id,
                    DocumentIndex.workspace_id == document.workspace_id,
                    DocumentIndex.profile == self.profile,
                )
            )
            if retry_key is not None:
                if index is None:
                    index = DocumentIndex(
                        workspace_id=document.workspace_id,
                        document_version_id=document.active_version_id,
                        profile=self.profile,
                        status="queued",
                        retry_key=retry_key,
                    )
                    session.add(index)
                    await session.flush()
                elif index.status == "failed" and index.retry_key != retry_key:
                    index.status = "queued"
                    index.retry_key = retry_key
                    index.failure_code = None
                    index.fence = None
                    index.lease_until = None
            return await self._index_view(session, index)

    async def ask(self, scope: Scope, key: str, question: str) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            # Serializes the workspace idempotency key and the bounded generation queue.
            workspace = await session.scalar(
                select(WorkspaceModel)
                .where(
                    WorkspaceModel.public_id == scope.workspace,
                    WorkspaceModel.status == "active",
                )
                .with_for_update()
            )
            if workspace is None:
                raise not_found()
            document = await self._document(session, scope, lock=True)
            existing = await session.scalar(
                select(RagQuestion).where(
                    RagQuestion.workspace_id == document.workspace_id,
                    RagQuestion.idempotency_key == key,
                )
            )
            if existing is not None:
                if (
                    existing.document_version_id != document.active_version_id
                    or existing.question != question
                ):
                    raise ApplicationError(
                        code="IDEMPOTENCY_CONFLICT",
                        message="请求标识已用于其他问题",
                        status_code=409,
                    )
                return question_view(existing)
            ready = await session.scalar(
                select(DocumentIndex.id).where(
                    DocumentIndex.workspace_id == document.workspace_id,
                    DocumentIndex.document_version_id == document.active_version_id,
                    DocumentIndex.profile == self.profile,
                    DocumentIndex.status == "ready",
                )
            )
            if ready is None:
                raise ApplicationError(
                    code="INDEX_NOT_READY", message="请先完成资料索引", status_code=409
                )
            recent = await session.scalar(
                select(func.count())
                .select_from(RagQuestion)
                .where(
                    RagQuestion.workspace_id == workspace.id,
                    RagQuestion.created_at > datetime.now(UTC) - timedelta(hours=1),
                )
            )
            pending = await session.scalar(
                select(func.count())
                .select_from(RagQuestion)
                .where(
                    RagQuestion.workspace_id == workspace.id,
                    RagQuestion.status.in_(["queued", "processing"]),
                )
            )
            if (recent or 0) >= 60 or (pending or 0) >= 3:
                raise ApplicationError(
                    code="QUESTION_LIMIT",
                    message="提问过于频繁，请等待当前回答完成后再试",
                    status_code=429,
                )
            queued = RagQuestion(
                workspace_id=document.workspace_id,
                document_version_id=document.active_version_id,
                profile=self.profile,
                idempotency_key=key,
                question=question,
                status="queued",
            )
            session.add(queued)
            await session.flush()
            return question_view(queued)

    async def _question(
        self, session: AsyncSession, scope: Scope, question_id: UUID
    ) -> RagQuestion:
        document = await self._document(session, scope)
        question = await session.scalar(
            select(RagQuestion).where(
                RagQuestion.public_id == question_id,
                RagQuestion.workspace_id == document.workspace_id,
                RagQuestion.document_version_id == document.active_version_id,
            )
        )
        if question is None:
            raise not_found()
        return question

    async def question(self, scope: Scope, question_id: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            return question_view(await self._question(session, scope, question_id))

    async def history(self, scope: Scope) -> list[dict[str, Any]]:
        async with self.sessions.begin() as session:
            document = await self._document(session, scope)
            questions = await session.scalars(
                select(RagQuestion)
                .where(
                    RagQuestion.workspace_id == document.workspace_id,
                    RagQuestion.document_version_id == document.active_version_id,
                )
                .order_by(RagQuestion.created_at.desc(), RagQuestion.id.desc())
                .limit(20)
            )
            return [question_view(question) for question in questions]

    async def cancel(self, scope: Scope, question_id: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            await self._document(session, scope, lock=True)
            question = await self._question(session, scope, question_id)
            if question.status in {"queued", "processing"}:
                question.status = "cancelled"
                question.fence = None
                question.lease_until = None
            return question_view(question)

    async def claim_index(self) -> Task | None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(DocumentIndex)
                .where(
                    DocumentIndex.status == "processing",
                    DocumentIndex.lease_until < datetime.now(UTC),
                )
                .values(status="failed", fence=None, failure_code="WORKER_INTERRUPTED")
            )
            row = (
                await session.execute(
                    select(DocumentIndex, DocumentModel, WorkspaceModel.public_id)
                    .join(
                        DocumentModel,
                        and_(
                            DocumentModel.active_version_id == DocumentIndex.document_version_id,
                            DocumentModel.workspace_id == DocumentIndex.workspace_id,
                        ),
                    )
                    .join(WorkspaceModel, WorkspaceModel.id == DocumentIndex.workspace_id)
                    .where(
                        DocumentIndex.status == "queued",
                        DocumentIndex.profile == self.profile,
                        DocumentModel.status == "ready",
                        DocumentModel.deleted_at.is_(None),
                        WorkspaceModel.status == "active",
                    )
                    .order_by(DocumentIndex.created_at)
                    .with_for_update(skip_locked=True, of=(DocumentIndex, DocumentModel))
                    .limit(1)
                )
            ).one_or_none()
            if row is None:
                return None
            index, document, workspace_public_id = row
            index.status = "processing"
            index.fence = uuid4()
            index.lease_until = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
            return Task(
                index.public_id,
                Scope(workspace_public_id, document.public_id),
                index.document_version_id,
                index.fence,
            )

    async def _task_index(self, session: AsyncSession, task: Task) -> DocumentIndex | None:
        try:
            document = await self._document(session, task.scope, lock=True)
        except ApplicationError:
            return None
        index: DocumentIndex | None = await session.scalar(
            select(DocumentIndex)
            .where(
                DocumentIndex.public_id == task.id,
                DocumentIndex.workspace_id == document.workspace_id,
                DocumentIndex.document_version_id == task.version,
                DocumentIndex.document_version_id == document.active_version_id,
                DocumentIndex.fence == task.fence,
                DocumentIndex.status == "processing",
                DocumentIndex.lease_until > datetime.now(UTC),
            )
            .with_for_update()
        )

        return index

    async def units(self, task: Task) -> list[tuple[int, str]] | None:
        async with self.sessions.begin() as session:
            index = await self._task_index(session, task)
            if index is None:
                return []
            if index.prepared:
                return None
            rows = (
                await session.execute(
                    select(DocumentPageModel.page_number, DocumentPageModel.content)
                    .where(
                        DocumentPageModel.document_version_id == task.version,
                        DocumentPageModel.workspace_id == index.workspace_id,
                    )
                    .order_by(DocumentPageModel.page_number)
                )
            ).all()
            return [(row[0], row[1]) for row in rows]

    async def prepare(self, task: Task, chunks: list[Chunk]) -> bool:
        async with self.sessions.begin() as session:
            index = await self._task_index(session, task)
            if index is None:
                return False
            if not index.prepared:
                session.add_all(
                    DocumentChunk(
                        index_id=index.id,
                        workspace_id=index.workspace_id,
                        ordinal=chunk.ordinal,
                        unit=chunk.unit,
                        start_offset=chunk.start,
                        end_offset=chunk.end,
                        content=chunk.content,
                    )
                    for chunk in chunks
                )
                index.prepared = True
            return True

    async def pending(self, task: Task) -> list[Evidence]:
        async with self.sessions.begin() as session:
            index = await self._task_index(session, task)
            if index is None:
                return []
            chunks = await session.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.index_id == index.id,
                    DocumentChunk.workspace_id == index.workspace_id,
                    DocumentChunk.embedding.is_(None),
                )
                .order_by(DocumentChunk.ordinal)
                .limit(16)
            )
            return [Evidence(chunk.public_id, chunk.content, chunk.unit, {}) for chunk in chunks]

    async def save_vectors(
        self, task: Task, sources: list[Evidence], vectors: list[list[float]]
    ) -> bool:
        async with self.sessions.begin() as session:
            index = await self._task_index(session, task)
            if index is None:
                return False
            for source, vector in zip(sources, vectors, strict=True):
                await session.execute(
                    update(DocumentChunk)
                    .where(
                        DocumentChunk.public_id == source.id,
                        DocumentChunk.index_id == index.id,
                        DocumentChunk.workspace_id == index.workspace_id,
                        DocumentChunk.embedding.is_(None),
                    )
                    .values(embedding=vector)
                )
            return True

    async def finish_index(self, task: Task) -> None:
        async with self.sessions.begin() as session:
            index = await self._task_index(session, task)
            if index is None:
                return
            remaining = await session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(
                    DocumentChunk.index_id == index.id,
                    DocumentChunk.workspace_id == index.workspace_id,
                    DocumentChunk.embedding.is_(None),
                )
            )
            index.status = "queued" if remaining else "ready"
            index.fence = None
            index.lease_until = None

    async def fail_index(self, task: Task, failure_code: str) -> None:
        async with self.sessions.begin() as session:
            index = await self._task_index(session, task)
            if index is not None:
                index.status = "failed"
                index.failure_code = failure_code
                index.failures = [
                    *index.failures[-9:],
                    {"code": failure_code, "at": datetime.now(UTC).isoformat()},
                ]
                index.fence = None
                index.lease_until = None

    async def claim_question(self) -> Task | None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(RagQuestion)
                .where(
                    RagQuestion.status == "processing",
                    RagQuestion.lease_until < datetime.now(UTC),
                )
                .values(status="failed", fence=None, failure_code="WORKER_INTERRUPTED")
            )
            row = (
                await session.execute(
                    select(RagQuestion, DocumentModel, WorkspaceModel.public_id)
                    .join(
                        DocumentModel,
                        and_(
                            DocumentModel.active_version_id == RagQuestion.document_version_id,
                            DocumentModel.workspace_id == RagQuestion.workspace_id,
                        ),
                    )
                    .join(WorkspaceModel, WorkspaceModel.id == RagQuestion.workspace_id)
                    .where(
                        RagQuestion.status == "queued",
                        RagQuestion.profile == self.profile,
                        DocumentModel.status == "ready",
                        DocumentModel.deleted_at.is_(None),
                        WorkspaceModel.status == "active",
                    )
                    .order_by(RagQuestion.created_at)
                    .with_for_update(skip_locked=True, of=(RagQuestion, DocumentModel))
                    .limit(1)
                )
            ).one_or_none()
            if row is None:
                return None
            question, document, workspace_public_id = row
            question.status = "processing"
            question.fence = uuid4()
            question.lease_until = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
            return Task(
                question.public_id,
                Scope(workspace_public_id, document.public_id),
                question.document_version_id,
                question.fence,
                question.question,
            )

    async def _task_question(self, session: AsyncSession, task: Task) -> RagQuestion | None:
        try:
            document = await self._document(session, task.scope, lock=True)
        except ApplicationError:
            return None
        question: RagQuestion | None = await session.scalar(
            select(RagQuestion)
            .where(
                RagQuestion.public_id == task.id,
                RagQuestion.workspace_id == document.workspace_id,
                RagQuestion.document_version_id == task.version,
                RagQuestion.document_version_id == document.active_version_id,
                RagQuestion.fence == task.fence,
                RagQuestion.status == "processing",
                RagQuestion.lease_until > datetime.now(UTC),
            )
            .with_for_update()
        )

        return question

    async def active(self, task: Task) -> bool:
        async with self.sessions.begin() as session:
            return await self._task_question(session, task) is not None

    async def retrieve(self, task: Task, vector: list[float]) -> list[Evidence]:
        async with self.sessions.begin() as session:
            question = await self._task_question(session, task)
            if question is None:
                return []
            distance = DocumentChunk.embedding.cosine_distance(vector)
            # Exact search over the scoped index avoids filtered ANN recall loss at MVP scale.
            query = (
                select(DocumentChunk, DocumentPageModel, distance.label("distance"))
                .join(
                    DocumentIndex,
                    and_(
                        DocumentIndex.id == DocumentChunk.index_id,
                        DocumentIndex.workspace_id == DocumentChunk.workspace_id,
                    ),
                )
                .join(
                    DocumentPageModel,
                    and_(
                        DocumentPageModel.document_version_id == DocumentIndex.document_version_id,
                        DocumentPageModel.workspace_id == DocumentChunk.workspace_id,
                        DocumentPageModel.page_number == DocumentChunk.unit,
                    ),
                )
                .where(
                    DocumentIndex.status == "ready",
                    DocumentIndex.profile == question.profile,
                    DocumentIndex.document_version_id == task.version,
                    DocumentChunk.workspace_id == question.workspace_id,
                    DocumentChunk.embedding.is_not(None),
                )
            )
            semantic = (
                await session.execute(query.order_by(distance, DocumentChunk.ordinal).limit(12))
            ).all()
            lexical_rank = func.ts_rank_cd(
                func.to_tsvector("simple", DocumentChunk.content),
                func.plainto_tsquery("simple", task.question),
            )
            lexical = (
                await session.execute(
                    query.where(lexical_rank > 0)
                    .order_by(lexical_rank.desc(), DocumentChunk.ordinal)
                    .limit(12)
                )
            ).all()
            scores: dict[UUID, float] = {}
            sources: dict[UUID, Evidence] = {}
            for rows in (semantic, lexical):
                for rank, (chunk, page, cosine_distance) in enumerate(rows, start=1):
                    # Model still decides sufficiency. Avoid feeding clearly unrelated candidates.
                    if float(cosine_distance) > 0.8:
                        continue
                    scores[chunk.public_id] = scores.get(chunk.public_id, 0) + 1 / (60 + rank)
                    sources[chunk.public_id] = Evidence(
                        chunk.public_id,
                        chunk.content,
                        chunk.unit,
                        {
                            "kind": page.locator_kind,
                            "position": page.locator_position,
                            "title": page.locator_title,
                            "path": page.locator_path,
                        },
                        1 - float(cosine_distance),
                    )
            return [
                sources[key]
                for key in sorted(scores, key=lambda key: scores[key], reverse=True)[:6]
            ]

    async def finish_question(
        self, task: Task, answer: dict[str, Any], usage: dict[str, Any]
    ) -> None:
        async with self.sessions.begin() as session:
            question = await self._task_question(session, task)
            if question is not None:
                question.answer = answer
                question.usage = {
                    **usage,
                    "retrieval_version": RETRIEVAL_VERSION,
                    "embedding_profile": question.profile,
                }
                question.status = "insufficient" if answer["insufficient_evidence"] else "answered"
                question.fence = None
                question.lease_until = None

    async def fail_question(self, task: Task, failure_code: str) -> None:
        async with self.sessions.begin() as session:
            question = await self._task_question(session, task)
            if question is not None:
                question.status = "failed"
                question.failure_code = failure_code
                question.fence = None
                question.lease_until = None
