from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApplicationError
from app.documents.infrastructure.models import DocumentModel, WorkspaceModel
from app.learning.attempt_store import SqlAttemptStore
from app.learning.models import (
    AnswerFeedback,
    Collection,
    CollectionDocument,
    Conversation,
    ConversationDocument,
    ConversationMessage,
    Quiz,
    QuizAttempt,
)
from app.learning.quiz_store import SqlQuizStore
from app.learning.run_models import AgentRun, AgentStageExecution
from app.learning.scope import (
    missing,
    resolve_scope,
    snapshot_ready,
    workspace_id,
)
from app.learning.workflow import GRAPH_VERSION
from app.rag.messages import FAILURES


def conversation_view(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": str(conversation.public_id),
        "title": conversation.title,
        "group_id": str(conversation.group_id) if conversation.group_id else None,
        "updated_at": conversation.updated_at,
        "scope": conversation.scope,
        "created_at": conversation.created_at,
    }


def message_view(message: ConversationMessage, rating: str | None = None) -> dict[str, Any]:
    return {
        "id": str(message.public_id),
        "question": message.question,
        "status": message.status,
        "scope": message.scope,
        "answer": message.answer,
        "task_result": message.task_result,
        "failure_code": message.failure_code,
        "failure_message": FAILURES.get(message.failure_code or ""),
        "feedback": rating,
        "created_at": message.created_at,
    }


class SqlLearningStore:
    """Persistence commands end their transactions before any model or embedding call."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        profile: str,
        *,
        wait_days: int = 7,
    ) -> None:
        self.sessions = sessions
        self.profile = profile
        self.wait_days = wait_days
        self.quizzes = SqlQuizStore(sessions, profile)
        self.attempts = SqlAttemptStore(sessions, profile)

    async def _workspace(self, session: AsyncSession, public_id: UUID) -> int:
        return await workspace_id(session, public_id)

    async def resume_learning(self, principal: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            active = exists().where(
                ConversationMessage.workspace_id == workspace,
                ConversationMessage.conversation_id == Conversation.id,
                ConversationMessage.status.in_(("queued", "processing")),
            )
            recent = (
                await session.execute(
                    select(Conversation, active.label("working"))
                    .where(Conversation.workspace_id == workspace)
                    .order_by(active.desc(), Conversation.updated_at.desc(), Conversation.id.desc())
                    .limit(1)
                )
            ).first()
            unfinished = (
                await session.execute(
                    select(QuizAttempt, Quiz)
                    .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
                    .where(
                        QuizAttempt.workspace_id == workspace,
                        Quiz.workspace_id == workspace,
                        Quiz.status == "ready",
                        QuizAttempt.status.in_(("in_progress", "grading")),
                    )
                    .order_by(QuizAttempt.created_at.desc(), QuizAttempt.id.desc())
                    .limit(1)
                )
            ).first()
            return {
                "conversation": {
                    "id": str(recent[0].public_id),
                    "title": recent[0].title,
                    "working": recent[1],
                }
                if recent
                else None,
                "attempt": {
                    "id": str(unfinished[0].public_id),
                    "quiz_id": str(unfinished[1].public_id),
                    "title": unfinished[1].title,
                    "status": unfinished[0].status,
                }
                if unfinished
                else None,
            }

    async def list_collections(self, principal: UUID) -> list[dict[str, Any]]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            collections = (
                await session.scalars(
                    select(Collection)
                    .where(Collection.workspace_id == workspace)
                    .order_by(Collection.created_at.desc())
                    .limit(100)
                )
            ).all()
            rows = (
                await session.execute(
                    select(CollectionDocument.collection_id, DocumentModel.public_id)
                    .join(
                        DocumentModel,
                        and_(
                            DocumentModel.id == CollectionDocument.document_id,
                            DocumentModel.workspace_id == CollectionDocument.workspace_id,
                        ),
                    )
                    .where(
                        CollectionDocument.workspace_id == workspace,
                        DocumentModel.deleted_at.is_(None),
                    )
                )
            ).all()
            members: dict[int, list[str]] = {item.id: [] for item in collections}
            for collection_id, document_id in rows:
                if collection_id in members:
                    members[collection_id].append(str(document_id))
            return [
                {
                    "id": str(item.public_id),
                    "name": item.name,
                    "description": item.description,
                    "document_ids": members[item.id],
                }
                for item in collections
            ]

    async def create_collection(
        self, principal: UUID, name: str, description: str, document_ids: list[UUID] | None = None
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            wanted = document_ids or []
            documents = (
                await session.scalars(
                    select(DocumentModel).where(
                        DocumentModel.workspace_id == workspace,
                        DocumentModel.public_id.in_(wanted),
                        DocumentModel.deleted_at.is_(None),
                    )
                )
            ).all()
            if {document.public_id for document in documents} != set(wanted):
                raise missing()
            item = Collection(workspace_id=workspace, name=name, description=description)
            session.add(item)
            await session.flush()
            session.add_all(
                CollectionDocument(
                    workspace_id=workspace, collection_id=item.id, document_id=document.id
                )
                for document in documents
            )
            return {
                "id": str(item.public_id),
                "name": item.name,
                "description": item.description,
                "document_ids": [str(document.public_id) for document in documents],
            }

    async def _collection(
        self, session: AsyncSession, workspace: int, public_id: UUID
    ) -> Collection:
        item = await session.scalar(
            select(Collection).where(
                Collection.workspace_id == workspace, Collection.public_id == public_id
            )
        )
        if item is None:
            raise missing()
        return item

    async def set_collection_documents(
        self,
        principal: UUID,
        collection_id: UUID,
        document_ids: list[UUID],
    ) -> dict[str, Any]:
        if len(document_ids) > 100 or len(set(document_ids)) != len(document_ids):
            raise ApplicationError(
                code="SCOPE_INVALID", message="集合最多包含 100 份不重复资料", status_code=422
            )
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            item = await self._collection(session, workspace, collection_id)
            documents = (
                await session.scalars(
                    select(DocumentModel).where(
                        DocumentModel.workspace_id == workspace,
                        DocumentModel.public_id.in_(document_ids),
                        DocumentModel.deleted_at.is_(None),
                    )
                )
            ).all()
            if {document.public_id for document in documents} != set(document_ids):
                raise missing()
            current = (
                await session.scalars(
                    select(CollectionDocument).where(
                        CollectionDocument.workspace_id == workspace,
                        CollectionDocument.collection_id == item.id,
                    )
                )
            ).all()
            wanted = {document.id for document in documents}
            for link in current:
                if link.document_id not in wanted:
                    await session.delete(link)
            existing = {link.document_id for link in current}
            session.add_all(
                CollectionDocument(
                    workspace_id=workspace, collection_id=item.id, document_id=document.id
                )
                for document in documents
                if document.id not in existing
            )
            return {
                "id": str(item.public_id),
                "name": item.name,
                "description": item.description,
                "document_ids": [str(document.public_id) for document in documents],
            }

    async def delete_collection(self, principal: UUID, collection_id: UUID) -> None:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            await session.delete(await self._collection(session, workspace, collection_id))

    async def _conversation(
        self, session: AsyncSession, workspace: int, public_id: UUID, *, lock: bool = False
    ) -> Conversation:
        query = select(Conversation).where(
            Conversation.workspace_id == workspace, Conversation.public_id == public_id
        )
        if lock:
            query = query.with_for_update()
        conversation = await session.scalar(query)
        if conversation is None:
            raise missing()
        return conversation

    async def create_conversation(
        self,
        principal: UUID,
        title: str,
        documents: list[UUID],
        collections: list[UUID],
        key: str | None = None,
    ) -> dict[str, Any]:
        create_request = {
            "title": title,
            "document_ids": sorted(str(value) for value in documents),
            "collection_ids": sorted(str(value) for value in collections),
        }
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            if key:
                await session.scalar(
                    select(WorkspaceModel.id)
                    .where(WorkspaceModel.id == workspace)
                    .with_for_update()
                )
                previous = await session.scalar(
                    select(Conversation).where(
                        Conversation.workspace_id == workspace, Conversation.create_key == key
                    )
                )
                if previous is not None:
                    if previous.create_request != create_request:
                        raise ApplicationError(
                            code="IDEMPOTENCY_CONFLICT",
                            message="此请求标识已用于其他新建对话请求",
                            status_code=409,
                        )
                    return conversation_view(previous)
            scope = (
                await resolve_scope(session, workspace, documents, collections, self.profile)
                if documents or collections
                else []
            )
            item = Conversation(
                workspace_id=workspace,
                title=title,
                scope=scope,
                create_key=key,
                create_request=create_request if key else None,
            )
            session.add(item)
            await session.flush()
            session.add_all(
                ConversationDocument(
                    workspace_id=workspace,
                    conversation_id=item.id,
                    document_version_id=entry["version_id"],
                )
                for entry in scope
            )
            return conversation_view(item)

    async def list_conversations(
        self, principal: UUID, before: UUID | None = None
    ) -> list[dict[str, Any]]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            query = select(Conversation).where(Conversation.workspace_id == workspace)
            if before:
                cursor = await self._conversation(session, workspace, before)
                query = query.where(
                    or_(
                        Conversation.updated_at < cursor.updated_at,
                        and_(
                            Conversation.updated_at == cursor.updated_at,
                            Conversation.id < cursor.id,
                        ),
                    )
                )
            items = (
                await session.scalars(
                    query.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(50)
                )
            ).all()
            return [conversation_view(item) for item in items]

    async def get_conversation(self, principal: UUID, conversation_id: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            item = await self._conversation(session, workspace, conversation_id)
            messages = (
                await session.scalars(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.workspace_id == workspace,
                        ConversationMessage.conversation_id == item.id,
                    )
                    .order_by(ConversationMessage.id)
                    .limit(100)
                )
            ).all()
            feedback = (
                await session.scalars(
                    select(AnswerFeedback).where(
                        AnswerFeedback.workspace_id == workspace,
                        AnswerFeedback.message_id.in_([message.id for message in messages]),
                    )
                )
            ).all()
            ratings = {entry.message_id: entry.rating for entry in feedback}
            runs = (
                await session.scalars(
                    select(AgentRun).where(
                        AgentRun.workspace_id == workspace, AgentRun.conversation_id == item.id
                    )
                )
            ).all()
            run_ids = {run.message_id: str(run.public_id) for run in runs}
            return {
                **conversation_view(item),
                "messages": [
                    {
                        **message_view(message, ratings.get(message.id)),
                        "run_id": run_ids.get(message.id),
                    }
                    for message in messages
                ],
            }

    async def set_conversation_scope(
        self,
        principal: UUID,
        conversation_id: UUID,
        documents: list[UUID],
        collections: list[UUID],
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            item = await self._conversation(session, workspace, conversation_id, lock=True)
            scope = await resolve_scope(session, workspace, documents, collections, self.profile)
            existing = set(
                await session.scalars(
                    select(ConversationDocument.document_version_id).where(
                        ConversationDocument.workspace_id == workspace,
                        ConversationDocument.conversation_id == item.id,
                    )
                )
            )
            session.add_all(
                ConversationDocument(
                    workspace_id=workspace,
                    conversation_id=item.id,
                    document_version_id=entry["version_id"],
                )
                for entry in scope
                if entry["version_id"] not in existing
            )
            item.scope = scope
            item.updated_at = datetime.now(UTC)
            return conversation_view(item)

    async def ask(
        self, principal: UUID, conversation_id: UUID, key: str, question: str
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            item = await self._conversation(session, workspace, conversation_id, lock=True)
            existing = await session.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.workspace_id == workspace,
                    ConversationMessage.idempotency_key == key,
                )
            )
            if existing is not None:
                if existing.conversation_id != item.id or existing.question != question:
                    raise ApplicationError(
                        code="IDEMPOTENCY_CONFLICT", message="请求标识已被使用", status_code=409
                    )
                run_id = await session.scalar(
                    select(AgentRun.public_id).where(
                        AgentRun.workspace_id == workspace, AgentRun.message_id == existing.id
                    )
                )
                return {**message_view(existing), "run_id": str(run_id) if run_id else None}
            if not item.scope or not await snapshot_ready(
                session, workspace, item.scope, self.profile
            ):
                raise ApplicationError(
                    code="SCOPE_NOT_READY",
                    message="资料范围已变化，请重新选择资料",
                    status_code=409,
                )
            pending = await session.scalar(
                select(func.count())
                .select_from(ConversationMessage)
                .where(
                    ConversationMessage.workspace_id == workspace,
                    ConversationMessage.status.in_(["queued", "processing"]),
                )
            )
            recent = await session.scalar(
                select(func.count())
                .select_from(ConversationMessage)
                .where(
                    ConversationMessage.workspace_id == workspace,
                    ConversationMessage.created_at > datetime.now(UTC) - timedelta(hours=1),
                )
            )
            if (pending or 0) >= 3 or (recent or 0) >= 60:
                raise ApplicationError(
                    code="QUESTION_LIMIT", message="提问过于频繁，请稍后重试", status_code=429
                )
            if item.title == "新对话" and not await session.scalar(
                select(ConversationMessage.id)
                .where(
                    ConversationMessage.workspace_id == workspace,
                    ConversationMessage.conversation_id == item.id,
                )
                .limit(1)
            ):
                item.title = question[:40]
            item.updated_at = datetime.now(UTC)
            message = ConversationMessage(
                workspace_id=workspace,
                conversation_id=item.id,
                idempotency_key=key,
                question=question,
                scope=item.scope,
                profile=self.profile,
                status="queued",
            )
            session.add(message)
            await session.flush()
            # Every message is executed as a durable run; there is no direct message worker.
            run = AgentRun(
                workspace_id=workspace,
                conversation_id=item.id,
                message_id=message.id,
                graph_version=GRAPH_VERSION,
                profile=self.profile,
                scope=item.scope,
                expires_at=datetime.now(UTC) + timedelta(days=self.wait_days),
            )
            session.add(run)
            await session.flush()
            return {**message_view(message), "run_id": str(run.public_id)}

    async def cancel_message(
        self, principal: UUID, conversation_id: UUID, message_id: UUID
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            item = await self._conversation(session, workspace, conversation_id)
            bound = await session.scalar(
                select(AgentRun)
                .join(
                    ConversationMessage,
                    and_(
                        ConversationMessage.id == AgentRun.message_id,
                        ConversationMessage.workspace_id == AgentRun.workspace_id,
                    ),
                )
                .where(
                    AgentRun.workspace_id == workspace,
                    AgentRun.conversation_id == item.id,
                    ConversationMessage.public_id == message_id,
                )
                .with_for_update(of=AgentRun)
            )
            message = await session.scalar(
                select(ConversationMessage)
                .where(
                    ConversationMessage.workspace_id == workspace,
                    ConversationMessage.conversation_id == item.id,
                    ConversationMessage.public_id == message_id,
                )
                .with_for_update()
            )
            if message is None:
                raise missing()
            if message.status in ("queued", "processing"):
                message.status = "cancelled"
                message.fence = None
                message.lease_until = None
            await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.workspace_id == workspace,
                    AgentRun.message_id == message.id,
                    AgentRun.status.not_in(["completed", "failed", "cancelled", "expired"]),
                )
                .values(
                    status="cancelled",
                    fence=None,
                    lease_until=None,
                    quiz_reserved=False,
                    revision=AgentRun.revision + 1,
                )
            )
            if bound is not None:
                await session.execute(
                    delete(AgentStageExecution).where(
                        AgentStageExecution.workspace_id == workspace,
                        AgentStageExecution.run_id == bound.id,
                    )
                )
                if bound.stage == "grade":
                    await session.execute(
                        update(QuizAttempt)
                        .where(
                            QuizAttempt.workspace_id == workspace,
                            QuizAttempt.id == bound.attempt_id,
                            QuizAttempt.status == "grading",
                        )
                        .values(
                            status="failed",
                            failure_code="AGENT_RUN_INACTIVE",
                            fence=None,
                            lease_until=None,
                        )
                    )
            return message_view(message)

    async def feedback(
        self, principal: UUID, conversation_id: UUID, message_id: UUID, key: str, rating: str
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            item = await self._conversation(session, workspace, conversation_id)
            message = await session.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.workspace_id == workspace,
                    ConversationMessage.conversation_id == item.id,
                    ConversationMessage.public_id == message_id,
                )
            )
            if message is None:
                raise missing()
            if message.status not in ("answered", "insufficient") or message.answer is None:
                raise ApplicationError(
                    code="ANSWER_NOT_READY", message="回答尚未完成", status_code=409
                )
            existing = await session.scalar(
                select(AnswerFeedback).where(
                    AnswerFeedback.workspace_id == workspace,
                    AnswerFeedback.message_id == message.id,
                )
            )
            if existing is not None:
                if existing.idempotency_key != key or existing.rating != rating:
                    raise ApplicationError(
                        code="IDEMPOTENCY_CONFLICT", message="该回答已有反馈", status_code=409
                    )
                return {"rating": existing.rating}
            reused = await session.scalar(
                select(AnswerFeedback.id).where(
                    AnswerFeedback.workspace_id == workspace, AnswerFeedback.idempotency_key == key
                )
            )
            if reused is not None:
                raise ApplicationError(
                    code="IDEMPOTENCY_CONFLICT", message="请求标识已被使用", status_code=409
                )
            session.add(
                AnswerFeedback(
                    workspace_id=workspace,
                    message_id=message.id,
                    idempotency_key=key,
                    rating=rating,
                )
            )
            return {"rating": rating}
