"""Exercise learning flows with synthetic data and fake models on an isolated database."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select, update

from app.core.auth import Principal, get_current_principal
from app.core.database import async_session_factory as sessions
from app.core.database import close_database
from app.core.errors import ApplicationError
from app.documents.api import get_document_service
from app.documents.application.service import DocumentService
from app.documents.infrastructure.models import (
    DocumentModel,
    DocumentPageModel,
    DocumentVersionModel,
    WorkspaceModel,
)
from app.documents.infrastructure.repository import SqlAlchemyDocumentsUnitOfWork
from app.learning.api import get_learning_service
from app.learning.application import LearningProcessor, LearningService
from app.learning.models import Conversation, Quiz, QuizAnswer, QuizAttempt
from app.learning.store import SqlLearningStore
from app.main import app
from app.rag.domain import Evidence
from app.rag.models import DocumentChunk, DocumentIndex

PROFILE = "learning-integration:1024:source-window-1500-180-v1"
VECTOR = [1.0] + [0.0] * 1023
CONTENT = (
    "The median resists extreme outliers in this example. "
    "A class initializes an object with __init__."
)


class UnusedStorage:
    async def upload(self, *, object_key: str, source_path: Path, length: int) -> None:
        raise AssertionError("Content reads must not upload objects")

    async def download(self, *, object_key: str, destination_path: Path) -> None:
        raise AssertionError("Content reads must not download objects")

    async def delete(self, *, object_key: str) -> None:
        raise AssertionError("Content reads must not delete objects")


class FakeModels:
    def __init__(self) -> None:
        self.answer_document_ids: set[UUID] = set()

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        unrelated = [0.0] * 1023 + [1.0]
        return [unrelated if text == "What is the exam date?" else VECTOR for text in texts]

    async def answer(
        self, question: str, sources: list[Evidence], *, history: list[dict[str, str]] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.answer_document_ids = {
            source.document_id for source in sources if source.document_id is not None
        }
        source = sources[0]
        return {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "The median resists outliers.",
                    "citations": [{"source_id": str(source.id), "quote": source.content[:46]}],
                }
            ],
        }, {"model": "fake", "history_count": len(history or [])}

    async def generate_quiz(
        self, config: dict[str, Any], sources: list[Evidence]
    ) -> dict[str, Any]:
        citation = {"source_id": str(sources[0].id), "quote": sources[0].content[:46]}
        return {
            "questions": [
                {
                    "kind": "single",
                    "topic": "robust statistics",
                    "stem": "Which statistic resists extreme outliers?",
                    "options": ["Median", "Mean", "Mode"],
                    "answer": "Median",
                    "explanation": "The cited sentence identifies the median as robust.",
                    "citations": [citation],
                },
                {
                    "kind": "short",
                    "topic": "robust statistics",
                    "stem": "Explain why median is used for outliers.",
                    "options": [],
                    "answer": "The median resists extreme outliers.",
                    "explanation": "It is robust against extreme values in the source.",
                    "citations": [citation],
                },
            ]
        }

    async def grade_short(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "question_id": item["question_id"],
                "score": 0.6,
                "feedback": "Partly correct; review the cited example.",
            }
            for item in items
        ]


async def seed_document(workspace: WorkspaceModel, name: str) -> UUID:
    async with sessions.begin() as session:
        document = DocumentModel(
            workspace_id=workspace.id,
            original_filename=name,
            media_type="application/pdf",
            byte_size=100,
            sha256=uuid4().hex * 2,
            object_key=f"learning-test/{uuid4()}",
            upload_idempotency_key=str(uuid4()),
            status="ready",
            page_count=1,
        )
        session.add(document)
        await session.flush()
        version = DocumentVersionModel(
            document_id=document.id,
            workspace_id=workspace.id,
            version_no=1,
            source_sha256=document.sha256,
            parser_name="fixture",
            parser_version="1",
            status="ready",
            page_count=1,
        )
        session.add(version)
        await session.flush()
        document.active_version_id = version.id
        session.add(
            DocumentPageModel(
                workspace_id=workspace.id,
                document_version_id=version.id,
                page_number=1,
                content=CONTENT,
                char_count=len(CONTENT),
                locator_kind="page",
                locator_position=1,
                locator_path=[],
            )
        )
        index = DocumentIndex(
            workspace_id=workspace.id,
            document_version_id=version.id,
            profile=PROFILE,
            status="ready",
            retry_key="seed",
            prepared=True,
        )
        session.add(index)
        await session.flush()
        session.add(
            DocumentChunk(
                workspace_id=workspace.id,
                index_id=index.id,
                ordinal=1,
                unit=1,
                start_offset=0,
                end_offset=len(CONTENT),
                content=CONTENT,
                embedding=VECTOR,
            )
        )
        return document.public_id


async def expect_error(coro: Any, code: str) -> None:
    try:
        await coro
        raise AssertionError(f"Expected {code}")
    except ApplicationError as exc:
        assert exc.code == code, exc.code


async def verify() -> None:
    workspace_id_1 = uuid4()
    workspace_id_2 = uuid4()
    async with sessions.begin() as session:
        owner = WorkspaceModel(public_id=workspace_id_1, name="learning fixture", status="active")
        foreign_owner = WorkspaceModel(
            public_id=workspace_id_2, name="foreign fixture", status="active"
        )
        session.add_all([owner, foreign_owner])
        await session.flush()
        owner_internal, foreign_internal = owner.id, foreign_owner.id
    owner = WorkspaceModel(
        id=owner_internal, public_id=workspace_id_1, name="learning fixture", status="active"
    )
    foreign_owner = WorkspaceModel(
        id=foreign_internal, public_id=workspace_id_2, name="foreign fixture", status="active"
    )
    first = await seed_document(owner, "statistics.pdf")
    second = await seed_document(owner, "class.pdf")
    foreign = await seed_document(foreign_owner, "foreign.pdf")
    store = SqlLearningStore(sessions, PROFILE)
    service = LearningService(store)
    fake_model = FakeModels()
    processor = LearningProcessor(store, FakeModels(), fake_model)
    try:
        collection = await service.create_collection(workspace_id_1, "Course", "Synthetic set")
        collection_id = UUID(collection["id"])
        await store.set_collection_documents(workspace_id_1, collection_id, [first, second])
        await expect_error(
            store.set_collection_documents(workspace_id_1, collection_id, [foreign]),
            "RESOURCE_NOT_FOUND",
        )
        conversation = await service.create_conversation(
            workspace_id_1, "Course questions", [], [collection_id]
        )
        conversation_id = UUID(conversation["id"])
        assert len(conversation["scope"]) == 2
        await expect_error(
            store.get_conversation(workspace_id_2, conversation_id), "RESOURCE_NOT_FOUND"
        )
        first_message = await service.ask(
            workspace_id_1, conversation_id, "one", "What resists outliers?"
        )
        assert (
            await service.ask(workspace_id_1, conversation_id, "one", "What resists outliers?")
        )["id"] == first_message["id"]
        await processor.process_message()
        assert fake_model.answer_document_ids == {first, second}
        detail = await store.get_conversation(workspace_id_1, conversation_id)
        assert detail["messages"][0]["status"] == "answered"
        citation = detail["messages"][0]["answer"]["claims"][0]["citations"][0]
        assert citation["document_id"] in {str(first), str(second)} and citation["version_id"]
        await store.feedback(
            workspace_id_1, conversation_id, UUID(first_message["id"]), "feedback-one", "helpful"
        )
        await store.feedback(
            workspace_id_1, conversation_id, UUID(first_message["id"]), "feedback-one", "helpful"
        )
        await expect_error(
            store.feedback(
                workspace_id_2,
                conversation_id,
                UUID(first_message["id"]),
                "foreign-feedback",
                "helpful",
            ),
            "RESOURCE_NOT_FOUND",
        )
        await expect_error(
            store.feedback(
                workspace_id_1,
                conversation_id,
                UUID(first_message["id"]),
                "feedback-one",
                "unhelpful",
            ),
            "IDEMPOTENCY_CONFLICT",
        )
        await store.set_conversation_scope(workspace_id_1, conversation_id, [second], [])
        second_message = await service.ask(
            workspace_id_1, conversation_id, "two", "How does it initialize?"
        )
        task = await store.claim_message()
        assert task is not None and task[4][0]["document_id"] == str(second)
        assert task[5] == []
        sources = await store.message_sources(task[0], task[1], VECTOR, task[3])
        assert sources and {source.document_id for source in sources} == {second}
        await store.cancel_message(workspace_id_1, conversation_id, UUID(second_message["id"]))
        await store.finish_message(
            task[0], task[1], {"insufficient_evidence": True, "claims": []}, {}
        )
        assert (await store.get_conversation(workspace_id_1, conversation_id))["messages"][1][
            "status"
        ] == "cancelled"
        refusal = await service.ask(
            workspace_id_1, conversation_id, "refusal", "What is the exam date?"
        )
        await processor.process_message()
        assert (await store.get_conversation(workspace_id_1, conversation_id))["messages"][2][
            "status"
        ] == "insufficient"
        assert refusal["id"]
        print("PASS: collections, scoped retrieval, scope switch, feedback, cancellation")

        config = {
            "type_counts": {"single": 1, "multiple": 0, "true_false": 0, "short": 1},
            "difficulty": "medium",
            "language": "en",
            "topic": "",
        }
        quiz = await service.create_quiz(
            workspace_id_1, "quiz-one", "Statistics Quiz", config, [first], []
        )
        quiz_id = UUID(quiz["id"])
        assert (
            await service.create_quiz(
                workspace_id_1, "quiz-one", "Statistics Quiz", config, [first], []
            )
        )["id"] == quiz["id"]
        await expect_error(
            service.create_quiz(
                workspace_id_1, "quiz-one", "Statistics Quiz", config, [second], []
            ),
            "IDEMPOTENCY_CONFLICT",
        )
        await processor.process_quiz()
        quiz_detail = await store.get_quiz(workspace_id_1, quiz_id)
        assert quiz_detail["status"] == "ready" and quiz_detail["question_count"] == 2
        assert all(
            question["answer"] is None and not question["sources"]
            for question in quiz_detail["questions"]
        )
        await expect_error(store.get_quiz(workspace_id_2, quiz_id), "RESOURCE_NOT_FOUND")
        attempt = await store.start_attempt(workspace_id_1, quiz_id, "attempt-one")
        attempt_id = UUID(attempt["id"])
        questions = quiz_detail["questions"]
        await store.save_answer(
            workspace_id_1, quiz_id, attempt_id, UUID(questions[0]["id"]), "Median"
        )
        await store.save_answer(
            workspace_id_1,
            quiz_id,
            attempt_id,
            UUID(questions[1]["id"]),
            "It does not move much with extreme values.",
        )
        assert (await store.submit_attempt(workspace_id_1, quiz_id, attempt_id))[
            "status"
        ] == "grading"
        await processor.process_grading()
        result = await store.get_attempt(workspace_id_1, quiz_id, attempt_id)
        assert result["status"] == "submitted" and result["score"] == 80.0
        assert result["weak_topics"] == ["robust statistics"]
        assert result["questions"][1]["grading_method"] == "model_hint"
        assert result["questions"][0]["sources"][0]["document_id"] == str(first)
        assert (await store.submit_attempt(workspace_id_1, quiz_id, attempt_id))["score"] == 80.0
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(QuizAnswer)) == 2
        print("PASS: quiz validation, answer privacy, grading, weak topics, idempotency")

        interrupted = await store.start_attempt(workspace_id_1, quiz_id, "attempt-interrupted")
        interrupted_id = UUID(interrupted["id"])
        await store.save_answer(
            workspace_id_1,
            quiz_id,
            interrupted_id,
            UUID(questions[1]["id"]),
            "The median is stable against extreme values.",
        )
        assert (await store.submit_attempt(workspace_id_1, quiz_id, interrupted_id))[
            "status"
        ] == "grading"
        claimed = await store.claim_grading()
        assert claimed is not None and claimed[0] == interrupted_id
        async with sessions.begin() as session:
            await session.execute(
                update(QuizAttempt)
                .where(QuizAttempt.public_id == interrupted_id)
                .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert await store.claim_grading() is None
        assert (await store.get_attempt(workspace_id_1, quiz_id, interrupted_id))[
            "status"
        ] == "failed"
        assert (await store.retry_grading(workspace_id_1, quiz_id, interrupted_id))[
            "status"
        ] == "grading"
        await processor.process_grading()
        assert (await store.get_attempt(workspace_id_1, quiz_id, interrupted_id))[
            "status"
        ] == "submitted"
        print("PASS: expired grading stays failed until explicit retry")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/collections")).status_code == 401
            app.dependency_overrides[get_current_principal] = lambda: Principal(workspace_id_1)
            app.dependency_overrides[get_learning_service] = lambda: service
            app.dependency_overrides[get_document_service] = lambda: DocumentService(
                unit_of_work_factory=lambda: SqlAlchemyDocumentsUnitOfWork(sessions),
                storage=UnusedStorage(),
                max_upload_bytes=100,
            )
            assert (await client.get(f"/api/v1/conversations/{conversation_id}")).status_code == 200
            assert (
                await client.post(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    headers={"Idempotency-Key": "invalid"},
                    json={"question": " "},
                )
            ).status_code == 422
            assert (await client.get(f"/api/v1/quizzes/{quiz_id}")).status_code == 200
            assert (
                await client.get(f"/api/v1/quizzes/{quiz_id}/attempts/{attempt_id}")
            ).status_code == 200
            async with sessions.begin() as session:
                document = await session.scalar(
                    select(DocumentModel).where(DocumentModel.public_id == first)
                )
                assert document is not None and document.active_version_id is not None
                original_version = document.active_version_id
                revision = DocumentVersionModel(
                    document_id=document.id,
                    workspace_id=owner_internal,
                    version_no=2,
                    source_sha256=document.sha256,
                    parser_name="fixture",
                    parser_version="2",
                    status="ready",
                    page_count=1,
                )
                session.add(revision)
                await session.flush()
                session.add(
                    DocumentPageModel(
                        workspace_id=owner_internal,
                        document_version_id=revision.id,
                        page_number=1,
                        content="The new parsing result.",
                        char_count=23,
                        locator_kind="page",
                        locator_position=1,
                        locator_path=[],
                    )
                )
                document.active_version_id = revision.id
            current = await client.get(f"/api/v1/documents/{first}/content?ordinal=1")
            historical = await client.get(
                f"/api/v1/documents/{first}/content?ordinal=1&version_id={original_version}"
            )
            assert current.status_code == historical.status_code == 200, (
                current.status_code,
                current.text,
                historical.status_code,
                historical.text,
            )
            assert current.json()["contents"][0]["content"] == "The new parsing result."
            assert historical.json()["contents"][0]["content"] == CONTENT
            assert (
                await client.get(
                    f"/api/v1/documents/{foreign}/content?ordinal=1&version_id={original_version}"
                )
            ).status_code == 404
            print("PASS: historical citations resolve to their original, workspace-scoped version")
        print("PASS: API authentication, contracts and invalid input")

        async with sessions.begin() as session:
            document = await session.scalar(
                select(DocumentModel).where(DocumentModel.public_id == first)
            )
            assert document is not None
            document.active_version_id = None
            await session.flush()
            await session.execute(
                delete(DocumentVersionModel).where(DocumentVersionModel.document_id == document.id)
            )
        async with sessions() as session:
            assert await session.scalar(select(Quiz.id).where(Quiz.public_id == quiz_id)) is None
            assert (
                await session.scalar(
                    select(Conversation.id).where(Conversation.public_id == conversation_id)
                )
                is None
            )
        print("PASS: deleting a source version removes dependent conversations and quizzes")
    finally:
        app.dependency_overrides.clear()
        async with sessions.begin() as session:
            documents = (
                await session.scalars(
                    select(DocumentModel).where(
                        DocumentModel.workspace_id.in_([owner_internal, foreign_internal])
                    )
                )
            ).all()
            for document in documents:
                document.active_version_id = None
            await session.flush()
            await session.execute(
                delete(DocumentModel).where(
                    DocumentModel.workspace_id.in_([owner_internal, foreign_internal])
                )
            )
            await session.execute(
                delete(WorkspaceModel).where(
                    WorkspaceModel.id.in_([owner_internal, foreign_internal])
                )
            )
        await close_database()


def main() -> None:
    asyncio.run(verify())


if __name__ == "__main__":
    main()
