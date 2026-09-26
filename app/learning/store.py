from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApplicationError
from app.documents.infrastructure.models import DocumentModel, WorkspaceModel
from app.learning.models import (
    AnswerFeedback,
    Collection,
    CollectionDocument,
    Conversation,
    ConversationDocument,
    ConversationMessage,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizDocument,
    QuizQuestion,
)
from app.learning.scope import (
    ScopeSnapshot,
    missing,
    quiz_sources,
    resolve_scope,
    retrieve_sources,
    snapshot_ready,
    workspace_id,
)
from app.rag.domain import Evidence, RagFailure
from app.rag.messages import FAILURES

LEASE_SECONDS = 180


def conversation_view(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": str(conversation.public_id),
        "title": conversation.title,
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


def quiz_view(quiz: Quiz, question_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(quiz.public_id),
        "title": quiz.title,
        "status": quiz.status,
        "config": quiz.config,
        "scope": quiz.scope,
        "question_count": question_count,
        "failure_code": quiz.failure_code,
        "failure_message": FAILURES.get(quiz.failure_code or ""),
        "created_at": quiz.created_at,
    }


def question_view(question: QuizQuestion, *, show_answer: bool) -> dict[str, Any]:
    return {
        "id": str(question.public_id),
        "ordinal": question.ordinal,
        "kind": question.kind,
        "difficulty": question.difficulty,
        "topic": question.topic,
        "stem": question.stem,
        "options": question.options,
        "sources": question.sources if show_answer else [],
        "answer": question.answer if show_answer else None,
        "explanation": question.explanation if show_answer else None,
    }


class SqlLearningStore:
    """Persistence commands end their transactions before any model or embedding call."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], profile: str) -> None:
        self.sessions = sessions
        self.profile = profile

    async def _workspace(self, session: AsyncSession, public_id: UUID) -> int:
        return await workspace_id(session, public_id)

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
        self, principal: UUID, name: str, description: str
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            item = Collection(workspace_id=workspace, name=name, description=description)
            session.add(item)
            await session.flush()
            return {
                "id": str(item.public_id),
                "name": item.name,
                "description": item.description,
                "document_ids": [],
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
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            scope = await resolve_scope(session, workspace, documents, collections, self.profile)
            item = Conversation(workspace_id=workspace, title=title, scope=scope)
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

    async def list_conversations(self, principal: UUID) -> list[dict[str, Any]]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            items = (
                await session.scalars(
                    select(Conversation)
                    .where(Conversation.workspace_id == workspace)
                    .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                    .limit(50)
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
            return {
                **conversation_view(item),
                "messages": [
                    message_view(message, ratings.get(message.id)) for message in messages
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
                return message_view(existing)
            if not await snapshot_ready(session, workspace, item.scope, self.profile):
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
            return message_view(message)

    async def cancel_message(
        self, principal: UUID, conversation_id: UUID, message_id: UUID
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            item = await self._conversation(session, workspace, conversation_id)
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

    async def claim_message(
        self,
    ) -> tuple[UUID, UUID, int, str, ScopeSnapshot, list[dict[str, str]]] | None:
        async with self.sessions.begin() as session:
            now = datetime.now(UTC)
            await session.execute(
                update(ConversationMessage)
                .where(
                    ConversationMessage.status == "processing",
                    ConversationMessage.lease_until < now,
                )
                .values(status="failed", fence=None, failure_code="WORKER_INTERRUPTED")
            )
            message = await session.scalar(
                select(ConversationMessage)
                .where(
                    ConversationMessage.status == "queued",
                    ConversationMessage.profile == self.profile,
                )
                .order_by(ConversationMessage.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if message is None:
                return None
            if not await snapshot_ready(
                session, message.workspace_id, message.scope, message.profile
            ):
                message.status = "failed"
                message.failure_code = "SCOPE_NOT_READY"
                return None
            message.status = "processing"
            message.fence = uuid4()
            message.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            previous = (
                await session.scalars(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.workspace_id == message.workspace_id,
                        ConversationMessage.conversation_id == message.conversation_id,
                        ConversationMessage.id < message.id,
                        ConversationMessage.status == "answered",
                    )
                    .order_by(ConversationMessage.id.desc())
                    .limit(10)
                )
            ).all()
            history = [
                {
                    "question": item.question,
                    "answer": (
                        " ".join(claim["text"] for claim in item.answer.get("claims", []))
                        if item.answer
                        else (item.task_result or {}).get("text", "")
                    )[:600],
                }
                for item in reversed(previous)
                if item.scope == message.scope and (item.answer or item.task_result)
            ]
            return (
                message.public_id,
                message.fence,
                message.workspace_id,
                message.question,
                message.scope,
                history[-3:],
            )

    async def _active_message(
        self, session: AsyncSession, public_id: UUID, fence: UUID
    ) -> ConversationMessage | None:
        message = await session.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.public_id == public_id,
                ConversationMessage.fence == fence,
                ConversationMessage.status == "processing",
                ConversationMessage.lease_until > datetime.now(UTC),
            )
            .with_for_update()
        )
        if message is None or not await snapshot_ready(
            session, message.workspace_id, message.scope, message.profile
        ):
            return None
        return message

    async def message_sources(
        self, public_id: UUID, fence: UUID, vector: list[float], query_text: str
    ) -> list[Evidence]:
        async with self.sessions.begin() as session:
            message = await self._active_message(session, public_id, fence)
            if message is None:
                return []
            return await retrieve_sources(
                session, message.workspace_id, message.scope, message.profile, vector, query_text
            )

    async def ensure_message_active(self, public_id: UUID, fence: UUID) -> None:
        async with self.sessions.begin() as session:
            if await self._active_message(session, public_id, fence) is None:
                raise RagFailure("AGENT_RUN_INACTIVE", "消息已停止或资料范围不可用")

    async def _check_quiz_capacity(self, session: AsyncSession, workspace: int) -> None:
        await session.scalar(
            select(WorkspaceModel.id).where(WorkspaceModel.id == workspace).with_for_update()
        )
        since = datetime.now(UTC) - timedelta(hours=1)
        quizzes = await session.scalar(
            select(func.count())
            .select_from(Quiz)
            .where(
                Quiz.workspace_id == workspace,
                Quiz.created_at > since,
            )
        )
        reserved = await session.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .where(
                ConversationMessage.workspace_id == workspace,
                ConversationMessage.status.in_(["queued", "processing"]),
                ConversationMessage.task_result["kind"].astext == "quiz",
            )
        )
        if (quizzes or 0) + (reserved or 0) >= 20:
            raise ApplicationError(
                code="QUIZ_LIMIT", message="出题过于频繁，请稍后重试", status_code=429
            )

    async def prepare_message_quiz(self, public_id: UUID, fence: UUID, title: str) -> None:
        async with self.sessions.begin() as session:
            message = await self._active_message(session, public_id, fence)
            if message is None:
                raise RagFailure("AGENT_RUN_INACTIVE", "任务已停止")
            if message.task_result is None:
                try:
                    await self._check_quiz_capacity(session, message.workspace_id)
                except ApplicationError as exc:
                    raise RagFailure(exc.code, exc.message) from exc
                message.task_result = {
                    "kind": "quiz",
                    "title": title,
                    "text": "正在检索依据并生成练习。",
                }

    async def finish_message_task(
        self, public_id: UUID, fence: UUID, result: dict[str, Any], usage: dict[str, Any]
    ) -> None:
        async with self.sessions.begin() as session:
            message = await self._active_message(session, public_id, fence)
            if message is not None:
                if result.get("quiz_id"):
                    quiz = await session.scalar(
                        select(Quiz).where(
                            Quiz.workspace_id == message.workspace_id,
                            Quiz.public_id == UUID(result["quiz_id"]),
                            Quiz.scope == message.scope,
                        )
                    )
                    if quiz is None:
                        raise RagFailure("AGENT_RUN_INACTIVE", "复习来源不可用")
                    message.quiz_id = quiz.id
                message.task_result = result
                message.usage = usage
                message.status = "answered"
                message.fence = None
                message.lease_until = None

    async def finish_message_quiz(
        self,
        public_id: UUID,
        fence: UUID,
        title: str,
        config: dict[str, Any],
        questions: list[dict[str, Any]],
        usage: dict[str, Any],
    ) -> None:
        async with self.sessions.begin() as session:
            message = await self._active_message(session, public_id, fence)
            if message is None:
                return
            quiz = Quiz(
                workspace_id=message.workspace_id,
                idempotency_key=f"agent-message:{public_id}",
                title=title,
                config=config,
                requested_scope={
                    "document_ids": sorted(item["document_id"] for item in message.scope),
                    "collection_ids": [],
                },
                scope=message.scope,
                profile=message.profile,
                status="ready",
                generation_usage=usage,
            )
            session.add(quiz)
            await session.flush()
            session.add_all(
                QuizDocument(
                    workspace_id=message.workspace_id,
                    quiz_id=quiz.id,
                    document_version_id=item["version_id"],
                )
                for item in message.scope
            )
            session.add_all(
                QuizQuestion(
                    workspace_id=message.workspace_id,
                    quiz_id=quiz.id,
                    ordinal=ordinal,
                    **question,
                )
                for ordinal, question in enumerate(questions, start=1)
            )
            message.quiz_id = quiz.id
            message.task_result = {
                "kind": "quiz",
                "quiz_id": str(quiz.public_id),
                "title": title,
                "text": f"已生成 {len(questions)} 题，可在这里作答；未通过校验的题目不会发布。",
            }
            message.usage = usage
            message.status = "answered"
            message.fence = None
            message.lease_until = None

    async def message_review_context(self, public_id: UUID, fence: UUID) -> dict[str, Any] | None:
        async with self.sessions.begin() as session:
            message = await self._active_message(session, public_id, fence)
            if message is None:
                raise RagFailure("AGENT_RUN_INACTIVE", "任务已停止")
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
                        Quiz.workspace_id == message.workspace_id,
                        Quiz.scope == message.scope,
                        QuizAttempt.workspace_id == message.workspace_id,
                        QuizAttempt.status == "submitted",
                    )
                    .order_by(QuizAttempt.submitted_at.desc(), QuizAttempt.id.desc())
                    .limit(1)
                )
            ).first()
            if row is None:
                return None
            quiz, attempt = row
            wrong = (
                await session.execute(
                    select(QuizQuestion, QuizAnswer)
                    .outerjoin(
                        QuizAnswer,
                        and_(
                            QuizAnswer.question_id == QuizQuestion.id,
                            QuizAnswer.workspace_id == message.workspace_id,
                            QuizAnswer.attempt_id == attempt.id,
                        ),
                    )
                    .where(
                        QuizQuestion.workspace_id == message.workspace_id,
                        QuizQuestion.quiz_id == quiz.id,
                        func.coalesce(QuizAnswer.score, 0) < 0.7,
                    )
                    .order_by(QuizQuestion.ordinal)
                    .limit(3)
                )
            ).all()
            summary = "\n".join(
                f"第 {question.ordinal} 题：{question.stem[:240]}；"
                f"你的答案：{str(answer.response)[:120] if answer else '未作答'}；"
                f"参考答案：{str(question.answer)[:120]}；解析：{question.explanation[:400]}"
                for question, answer in wrong
            )[:1800]
            return {
                "quiz_id": str(quiz.public_id),
                "attempt_id": str(attempt.public_id),
                "title": quiz.title,
                "weak_topics": attempt.weak_topics or [],
                "summary": summary,
            }

    async def finish_message(
        self, public_id: UUID, fence: UUID, answer: dict[str, Any], usage: dict[str, Any]
    ) -> None:
        async with self.sessions.begin() as session:
            message = await self._active_message(session, public_id, fence)
            if message is not None:
                message.answer = answer
                message.usage = usage
                message.status = "insufficient" if answer["insufficient_evidence"] else "answered"
                message.fence = None
                message.lease_until = None

    async def fail_message(self, public_id: UUID, fence: UUID, code: str) -> None:
        async with self.sessions.begin() as session:
            message = await session.scalar(
                select(ConversationMessage)
                .where(
                    ConversationMessage.public_id == public_id,
                    ConversationMessage.fence == fence,
                    ConversationMessage.status == "processing",
                )
                .with_for_update()
            )
            if message is not None:
                message.status = "failed"
                message.failure_code = code
                message.fence = None
                message.lease_until = None

    async def create_quiz(
        self,
        principal: UUID,
        key: str,
        title: str,
        config: dict[str, Any],
        documents: list[UUID],
        collections: list[UUID],
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            # Serialize replay lookup and creation, including requests using the same key.
            await session.scalar(
                select(WorkspaceModel.id).where(WorkspaceModel.id == workspace).with_for_update()
            )
            requested_scope = {
                "document_ids": sorted({str(item) for item in documents}),
                "collection_ids": sorted({str(item) for item in collections}),
            }
            existing = await session.scalar(
                select(Quiz).where(Quiz.workspace_id == workspace, Quiz.idempotency_key == key)
            )
            if existing is not None:
                if (
                    existing.config != config
                    or existing.title != title
                    or existing.requested_scope != requested_scope
                ):
                    raise ApplicationError(
                        code="IDEMPOTENCY_CONFLICT", message="请求标识已被使用", status_code=409
                    )
                return quiz_view(existing, await self._question_count(session, existing.id))
            await self._check_quiz_capacity(session, workspace)
            scope = await resolve_scope(session, workspace, documents, collections, self.profile)
            item = Quiz(
                workspace_id=workspace,
                idempotency_key=key,
                title=title,
                config=config,
                requested_scope=requested_scope,
                scope=scope,
                profile=self.profile,
                status="queued",
            )
            session.add(item)
            await session.flush()
            session.add_all(
                QuizDocument(
                    workspace_id=workspace, quiz_id=item.id, document_version_id=entry["version_id"]
                )
                for entry in scope
            )
            return quiz_view(item)

    async def _quiz(
        self, session: AsyncSession, workspace: int, public_id: UUID, *, lock: bool = False
    ) -> Quiz:
        query = select(Quiz).where(Quiz.workspace_id == workspace, Quiz.public_id == public_id)
        if lock:
            query = query.with_for_update()
        item = await session.scalar(query)
        if item is None:
            raise missing()
        return item

    async def _question_count(self, session: AsyncSession, quiz_id: int) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(QuizQuestion)
                .where(QuizQuestion.quiz_id == quiz_id)
            )
            or 0
        )

    async def list_quizzes(self, principal: UUID) -> list[dict[str, Any]]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            items = (
                await session.scalars(
                    select(Quiz)
                    .where(Quiz.workspace_id == workspace)
                    .order_by(Quiz.created_at.desc(), Quiz.id.desc())
                    .limit(50)
                )
            ).all()
            return [quiz_view(item, await self._question_count(session, item.id)) for item in items]

    async def get_quiz(self, principal: UUID, quiz_id: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            item = await self._quiz(session, workspace, quiz_id)
            questions = (
                await session.scalars(
                    select(QuizQuestion)
                    .where(QuizQuestion.workspace_id == workspace, QuizQuestion.quiz_id == item.id)
                    .order_by(QuizQuestion.ordinal)
                )
            ).all()
            return {
                **quiz_view(item, len(questions)),
                "questions": [question_view(question, show_answer=False) for question in questions],
            }

    async def claim_quiz(self) -> tuple[UUID, UUID, int, dict[str, Any], ScopeSnapshot] | None:
        async with self.sessions.begin() as session:
            now = datetime.now(UTC)
            await session.execute(
                update(Quiz)
                .where(Quiz.status == "processing", Quiz.lease_until < now)
                .values(status="failed", fence=None, failure_code="WORKER_INTERRUPTED")
            )
            quiz = await session.scalar(
                select(Quiz)
                .where(Quiz.status == "queued", Quiz.profile == self.profile)
                .order_by(Quiz.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if quiz is None:
                return None
            if not await snapshot_ready(session, quiz.workspace_id, quiz.scope, quiz.profile):
                quiz.status = "failed"
                quiz.failure_code = "SCOPE_NOT_READY"
                return None
            quiz.status = "processing"
            quiz.fence = uuid4()
            quiz.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            return quiz.public_id, quiz.fence, quiz.workspace_id, quiz.config, quiz.scope

    async def _active_quiz(
        self, session: AsyncSession, public_id: UUID, fence: UUID
    ) -> Quiz | None:
        quiz = await session.scalar(
            select(Quiz)
            .where(
                Quiz.public_id == public_id,
                Quiz.fence == fence,
                Quiz.status == "processing",
                Quiz.lease_until > datetime.now(UTC),
            )
            .with_for_update()
        )
        if quiz is None or not await snapshot_ready(
            session, quiz.workspace_id, quiz.scope, quiz.profile
        ):
            return None
        return quiz

    async def ensure_quiz_active(self, public_id: UUID, fence: UUID) -> None:
        async with self.sessions.begin() as session:
            if await self._active_quiz(session, public_id, fence) is None:
                raise RagFailure("AGENT_RUN_INACTIVE", "出题任务已停止或资料范围不可用")

    async def quiz_evidence(
        self, public_id: UUID, fence: UUID, topic_vector: list[float] | None = None, topic: str = ""
    ) -> list[Evidence]:
        async with self.sessions.begin() as session:
            quiz = await self._active_quiz(session, public_id, fence)
            if quiz is None:
                return []
            if topic_vector is not None:
                return await retrieve_sources(
                    session,
                    quiz.workspace_id,
                    quiz.scope,
                    quiz.profile,
                    topic_vector,
                    topic,
                    limit=12,
                )
            return await quiz_sources(session, quiz.workspace_id, quiz.scope, quiz.profile)

    async def finish_quiz(
        self,
        public_id: UUID,
        fence: UUID,
        questions: list[dict[str, Any]],
        usage: dict[str, Any] | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            quiz = await self._active_quiz(session, public_id, fence)
            if quiz is None:
                return
            session.add_all(
                QuizQuestion(
                    workspace_id=quiz.workspace_id, quiz_id=quiz.id, ordinal=ordinal, **question
                )
                for ordinal, question in enumerate(questions, start=1)
            )
            quiz.status = "ready"
            quiz.generation_usage = usage
            quiz.fence = None
            quiz.lease_until = None

    async def fail_quiz(self, public_id: UUID, fence: UUID, code: str) -> None:
        async with self.sessions.begin() as session:
            quiz = await session.scalar(
                select(Quiz)
                .where(
                    Quiz.public_id == public_id, Quiz.fence == fence, Quiz.status == "processing"
                )
                .with_for_update()
            )
            if quiz is not None:
                quiz.status = "failed"
                quiz.failure_code = code
                quiz.fence = None
                quiz.lease_until = None

    async def start_attempt(self, principal: UUID, quiz_id: UUID, key: str) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            quiz = await self._quiz(session, workspace, quiz_id)
            if quiz.status != "ready":
                raise ApplicationError(
                    code="QUIZ_NOT_READY", message="Quiz 尚未生成完成", status_code=409
                )
            existing = await session.scalar(
                select(QuizAttempt).where(
                    QuizAttempt.workspace_id == workspace, QuizAttempt.idempotency_key == key
                )
            )
            if existing is not None:
                if existing.quiz_id != quiz.id:
                    raise ApplicationError(
                        code="IDEMPOTENCY_CONFLICT", message="请求标识已被使用", status_code=409
                    )
                return {"id": str(existing.public_id), "status": existing.status}
            attempt = QuizAttempt(
                workspace_id=workspace, quiz_id=quiz.id, idempotency_key=key, status="in_progress"
            )
            session.add(attempt)
            await session.flush()
            return {"id": str(attempt.public_id), "status": attempt.status}

    async def _attempt(
        self,
        session: AsyncSession,
        workspace: int,
        quiz: Quiz,
        public_id: UUID,
        *,
        lock: bool = False,
    ) -> QuizAttempt:
        query = select(QuizAttempt).where(
            QuizAttempt.workspace_id == workspace,
            QuizAttempt.quiz_id == quiz.id,
            QuizAttempt.public_id == public_id,
        )
        if lock:
            query = query.with_for_update()
        attempt = await session.scalar(query)
        if attempt is None:
            raise missing()
        return attempt

    async def save_answer(
        self, principal: UUID, quiz_id: UUID, attempt_id: UUID, question_id: UUID, response: Any
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            quiz = await self._quiz(session, workspace, quiz_id)
            attempt = await self._attempt(session, workspace, quiz, attempt_id, lock=True)
            if attempt.status != "in_progress":
                raise ApplicationError(code="ATTEMPT_CLOSED", message="作答已提交", status_code=409)
            question = await session.scalar(
                select(QuizQuestion).where(
                    QuizQuestion.workspace_id == workspace,
                    QuizQuestion.quiz_id == quiz.id,
                    QuizQuestion.public_id == question_id,
                )
            )
            if question is None:
                raise missing()
            if question.kind == "single" and (
                not isinstance(response, str) or response not in question.options
            ):
                raise ApplicationError(
                    code="ANSWER_INVALID", message="请选择一个有效选项", status_code=422
                )
            if question.kind == "multiple" and (
                not isinstance(response, list)
                or not response
                or len(response) != len(set(map(str, response)))
                or any(
                    not isinstance(value, str) or value not in question.options
                    for value in response
                )
            ):
                raise ApplicationError(
                    code="ANSWER_INVALID", message="请选择有效选项", status_code=422
                )
            if question.kind == "true_false" and type(response) is not bool:
                raise ApplicationError(
                    code="ANSWER_INVALID", message="请选择正确或错误", status_code=422
                )
            if question.kind == "short" and (
                not isinstance(response, str) or len(response.strip()) > 2000
            ):
                raise ApplicationError(
                    code="ANSWER_INVALID", message="简答最多 2000 字", status_code=422
                )
            answer = await session.scalar(
                select(QuizAnswer).where(
                    QuizAnswer.workspace_id == workspace,
                    QuizAnswer.attempt_id == attempt.id,
                    QuizAnswer.question_id == question.id,
                )
            )
            if answer is None:
                answer = QuizAnswer(
                    workspace_id=workspace,
                    attempt_id=attempt.id,
                    question_id=question.id,
                    response=response,
                )
                session.add(answer)
            else:
                answer.response = response
            return {"question_id": str(question.public_id), "response": response}

    async def _finalize_attempt(self, session: AsyncSession, attempt: QuizAttempt) -> None:
        questions = (
            await session.scalars(
                select(QuizQuestion).where(
                    QuizQuestion.workspace_id == attempt.workspace_id,
                    QuizQuestion.quiz_id == attempt.quiz_id,
                )
            )
        ).all()
        answers = (
            await session.scalars(
                select(QuizAnswer).where(
                    QuizAnswer.workspace_id == attempt.workspace_id,
                    QuizAnswer.attempt_id == attempt.id,
                )
            )
        ).all()
        scores = {answer.question_id: answer.score or 0 for answer in answers}
        attempt.score = round(
            100
            * sum(scores.get(question.id, 0) for question in questions)
            / max(len(questions), 1),
            1,
        )
        attempt.weak_topics = sorted(
            {question.topic for question in questions if scores.get(question.id, 0) < 0.7}
        )
        attempt.status = "submitted"
        attempt.submitted_at = datetime.now(UTC)
        attempt.fence = None
        attempt.lease_until = None

    async def submit_attempt(
        self, principal: UUID, quiz_id: UUID, attempt_id: UUID
    ) -> dict[str, Any]:
        from app.learning.domain import score_objective

        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            quiz = await self._quiz(session, workspace, quiz_id)
            attempt = await self._attempt(session, workspace, quiz, attempt_id, lock=True)
            if attempt.status != "in_progress":
                return {
                    "id": str(attempt.public_id),
                    "status": attempt.status,
                    "score": attempt.score,
                    "weak_topics": attempt.weak_topics,
                }
            answers = (
                await session.scalars(
                    select(QuizAnswer).where(
                        QuizAnswer.workspace_id == workspace, QuizAnswer.attempt_id == attempt.id
                    )
                )
            ).all()
            questions = {
                question.id: question
                for question in (
                    await session.scalars(
                        select(QuizQuestion).where(
                            QuizQuestion.workspace_id == workspace, QuizQuestion.quiz_id == quiz.id
                        )
                    )
                ).all()
            }
            needs_grading = False
            for answer in answers:
                question = questions[answer.question_id]
                if question.kind == "short":
                    if answer.response.strip():
                        needs_grading = True
                    else:
                        answer.score = 0
                        answer.feedback = "未作答"
                        answer.grading_method = "automatic"
                else:
                    answer.score = score_objective(question.kind, question.answer, answer.response)
                    answer.feedback = "正确" if answer.score == 1 else "请对照来源与解析复习"
                    answer.grading_method = "automatic"
            if needs_grading:
                attempt.status = "grading"
            else:
                await session.flush()
                await self._finalize_attempt(session, attempt)
            return {
                "id": str(attempt.public_id),
                "status": attempt.status,
                "score": attempt.score,
                "weak_topics": attempt.weak_topics,
            }

    async def retry_grading(
        self, principal: UUID, quiz_id: UUID, attempt_id: UUID
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            quiz = await self._quiz(session, workspace, quiz_id)
            attempt = await self._attempt(session, workspace, quiz, attempt_id, lock=True)
            if attempt.status == "failed" and attempt.failure_code in {
                "PROVIDER_TIMEOUT",
                "PROVIDER_LIMIT",
                "PROVIDER_UNAVAILABLE",
                "GRADING_INVALID",
                "GRADING_FAILED",
                "WORKER_INTERRUPTED",
            }:
                if not await snapshot_ready(session, workspace, quiz.scope, quiz.profile):
                    raise ApplicationError(
                        code="SCOPE_NOT_READY", message="资料范围已变化", status_code=409
                    )
                attempt.status = "grading"
                attempt.failure_code = None
                attempt.fence = None
                attempt.lease_until = None
            elif attempt.status not in {"grading", "submitted"}:
                raise ApplicationError(
                    code="GRADING_NOT_RETRYABLE", message="此作答无法重新评分", status_code=409
                )
            return {
                "id": str(attempt.public_id),
                "status": attempt.status,
                "score": attempt.score,
                "weak_topics": attempt.weak_topics,
            }

    async def get_attempt(self, principal: UUID, quiz_id: UUID, attempt_id: UUID) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            quiz = await self._quiz(session, workspace, quiz_id)
            attempt = await self._attempt(session, workspace, quiz, attempt_id)
            questions = (
                await session.scalars(
                    select(QuizQuestion)
                    .where(QuizQuestion.workspace_id == workspace, QuizQuestion.quiz_id == quiz.id)
                    .order_by(QuizQuestion.ordinal)
                )
            ).all()
            answers = (
                await session.scalars(
                    select(QuizAnswer).where(
                        QuizAnswer.workspace_id == workspace, QuizAnswer.attempt_id == attempt.id
                    )
                )
            ).all()
            by_question = {answer.question_id: answer for answer in answers}
            complete = attempt.status == "submitted"
            return {
                "id": str(attempt.public_id),
                "status": attempt.status,
                "score": attempt.score,
                "weak_topics": attempt.weak_topics,
                "failure_code": attempt.failure_code,
                "questions": [
                    {
                        **question_view(question, show_answer=complete),
                        "response": by_question[question.id].response
                        if question.id in by_question
                        else None,
                        "earned": by_question[question.id].score
                        if complete and question.id in by_question
                        else None,
                        "feedback": by_question[question.id].feedback
                        if complete and question.id in by_question
                        else None,
                        "grading_method": by_question[question.id].grading_method
                        if complete and question.id in by_question
                        else None,
                    }
                    for question in questions
                ],
            }

    async def list_attempts(self, principal: UUID, quiz_id: UUID) -> list[dict[str, Any]]:
        async with self.sessions.begin() as session:
            workspace = await self._workspace(session, principal)
            quiz = await self._quiz(session, workspace, quiz_id)
            attempts = (
                await session.scalars(
                    select(QuizAttempt)
                    .where(QuizAttempt.workspace_id == workspace, QuizAttempt.quiz_id == quiz.id)
                    .order_by(QuizAttempt.created_at.desc())
                    .limit(30)
                )
            ).all()
            return [
                {
                    "id": str(item.public_id),
                    "status": item.status,
                    "score": item.score,
                    "weak_topics": item.weak_topics,
                }
                for item in attempts
            ]

    async def claim_grading(self) -> tuple[UUID, UUID, list[dict[str, Any]]] | None:
        async with self.sessions.begin() as session:
            now = datetime.now(UTC)
            await session.execute(
                update(QuizAttempt)
                .where(
                    QuizAttempt.status == "grading",
                    QuizAttempt.fence.is_not(None),
                    QuizAttempt.lease_until < now,
                )
                .values(
                    status="failed",
                    fence=None,
                    lease_until=None,
                    failure_code="WORKER_INTERRUPTED",
                )
            )
            attempt = await session.scalar(
                select(QuizAttempt)
                .where(
                    QuizAttempt.status == "grading",
                    QuizAttempt.fence.is_(None),
                )
                .order_by(QuizAttempt.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if attempt is None:
                return None
            quiz = await session.scalar(
                select(Quiz).where(
                    Quiz.id == attempt.quiz_id, Quiz.workspace_id == attempt.workspace_id
                )
            )
            if quiz is None or not await snapshot_ready(
                session, attempt.workspace_id, quiz.scope, quiz.profile
            ):
                attempt.status = "failed"
                attempt.failure_code = "SCOPE_NOT_READY"
                return None
            attempt.fence = uuid4()
            attempt.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            rows = (
                await session.execute(
                    select(QuizAnswer, QuizQuestion)
                    .join(
                        QuizQuestion,
                        and_(
                            QuizQuestion.id == QuizAnswer.question_id,
                            QuizQuestion.workspace_id == QuizAnswer.workspace_id,
                        ),
                    )
                    .where(
                        QuizAnswer.workspace_id == attempt.workspace_id,
                        QuizAnswer.attempt_id == attempt.id,
                        QuizQuestion.kind == "short",
                        QuizAnswer.score.is_(None),
                    )
                )
            ).all()
            return (
                attempt.public_id,
                attempt.fence,
                [
                    {
                        "question_id": str(question.public_id),
                        "stem": question.stem,
                        "reference": question.answer,
                        "explanation": question.explanation,
                        "response": answer.response,
                        "sources": question.sources,
                    }
                    for answer, question in rows
                ],
            )

    async def finish_grading(
        self, public_id: UUID, fence: UUID, grades: list[dict[str, Any]]
    ) -> None:
        async with self.sessions.begin() as session:
            attempt = await session.scalar(
                select(QuizAttempt)
                .where(
                    QuizAttempt.public_id == public_id,
                    QuizAttempt.fence == fence,
                    QuizAttempt.status == "grading",
                    QuizAttempt.lease_until > datetime.now(UTC),
                )
                .with_for_update()
            )
            if attempt is None:
                return
            rows = (
                await session.execute(
                    select(QuizAnswer, QuizQuestion.public_id)
                    .join(
                        QuizQuestion,
                        and_(
                            QuizQuestion.id == QuizAnswer.question_id,
                            QuizQuestion.workspace_id == QuizAnswer.workspace_id,
                        ),
                    )
                    .where(
                        QuizAnswer.workspace_id == attempt.workspace_id,
                        QuizAnswer.attempt_id == attempt.id,
                        QuizQuestion.kind == "short",
                        QuizAnswer.score.is_(None),
                    )
                )
            ).all()
            grade_map = {grade["question_id"]: grade for grade in grades}
            if set(grade_map) != {str(question_id) for _, question_id in rows}:
                raise ValueError("Grading result does not match submitted answers")
            for answer, question_id in rows:
                grade = grade_map[str(question_id)]
                answer.score = grade["score"]
                answer.feedback = grade["feedback"]
                answer.grading_method = "model_hint"
            await session.flush()
            await self._finalize_attempt(session, attempt)

    async def fail_grading(self, public_id: UUID, fence: UUID, code: str) -> None:
        async with self.sessions.begin() as session:
            attempt = await session.scalar(
                select(QuizAttempt)
                .where(
                    QuizAttempt.public_id == public_id,
                    QuizAttempt.fence == fence,
                    QuizAttempt.status == "grading",
                )
                .with_for_update()
            )
            if attempt is not None:
                attempt.status = "failed"
                attempt.failure_code = code
                attempt.fence = None
                attempt.lease_until = None
