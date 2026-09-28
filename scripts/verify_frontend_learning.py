"""Verify library filtering, atomic collection creation and resume in an empty isolated database."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select, update

from app.core.auth import Principal, get_current_principal
from app.core.database import async_session_factory as sessions
from app.core.database import close_database
from app.documents.infrastructure.models import DocumentModel, WorkspaceModel
from app.learning.api import get_learning_service
from app.learning.application import LearningService
from app.learning.models import Collection, Conversation, ConversationMessage, Quiz, QuizAttempt
from app.learning.store import SqlLearningStore
from app.main import app
from scripts.verify_learning_flow import PROFILE, seed_document


async def verify() -> None:
    owners: list[UUID] = []
    async with sessions.begin() as session:
        if await session.scalar(select(func.count()).select_from(WorkspaceModel)):
            raise RuntimeError("Requires an empty isolated database; existing data was not changed")
        owner = WorkspaceModel(public_id=uuid4(), name="UI fixture", status="active")
        foreign = WorkspaceModel(public_id=uuid4(), name="foreign UI fixture", status="active")
        session.add_all([owner, foreign])
        await session.flush()
        owners = [owner.public_id, foreign.public_id]
    store = SqlLearningStore(sessions, PROFILE)
    app.dependency_overrides[get_current_principal] = lambda: Principal(owner.public_id)
    app.dependency_overrides[get_learning_service] = lambda: LearningService(store)
    try:
        first = await seed_document(owner, "Statistics.pdf")
        literal = await seed_document(owner, "Statistics_100%.pdf")
        pending = await seed_document(owner, "History.pdf")
        removed = await seed_document(owner, "Statistics removed.pdf")
        other = await seed_document(foreign, "Statistics.pdf")
        async with sessions.begin() as session:
            await session.execute(
                update(DocumentModel)
                .where(DocumentModel.workspace_id == owner.id)
                .values(created_at=datetime(2026, 9, 1, tzinfo=UTC))
            )
            await session.execute(
                update(DocumentModel)
                .where(DocumentModel.public_id == pending)
                .values(status="parsing")
            )
            await session.execute(
                update(DocumentModel)
                .where(DocumentModel.public_id == removed)
                .values(status="deleted", deleted_at=datetime.now(UTC))
            )
            active = Conversation(
                workspace_id=owner.id,
                title="Active task",
                scope=[],
                updated_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
            recent = Conversation(
                workspace_id=owner.id,
                title="New conversation",
                scope=[],
                updated_at=datetime(2026, 9, 2, tzinfo=UTC),
            )
            foreign_conversation = Conversation(
                workspace_id=foreign.id, title="Foreign task", scope=[]
            )
            session.add_all([active, recent, foreign_conversation])
            await session.flush()
            session.add_all(
                [
                    ConversationMessage(
                        workspace_id=owner.id,
                        conversation_id=active.id,
                        idempotency_key=str(uuid4()),
                        question="fixture request",
                        scope=[],
                        profile=PROFILE,
                        status="queued",
                    ),
                    ConversationMessage(
                        workspace_id=foreign.id,
                        conversation_id=foreign_conversation.id,
                        idempotency_key=str(uuid4()),
                        question="foreign fixture request",
                        scope=[],
                        profile=PROFILE,
                        status="queued",
                    ),
                ]
            )
            for workspace in (owner, foreign):
                quiz = Quiz(
                    workspace_id=workspace.id,
                    idempotency_key=str(uuid4()),
                    title="Unfinished practice",
                    scope=[],
                    requested_scope={},
                    config={},
                    profile=PROFILE,
                    status="ready",
                )
                session.add(quiz)
                await session.flush()
                attempt = QuizAttempt(
                    workspace_id=workspace.id,
                    quiz_id=quiz.id,
                    idempotency_key=str(uuid4()),
                    status="in_progress",
                )
                session.add(attempt)
                await session.flush()
                if workspace.id == owner.id:
                    own_quiz_id = str(quiz.public_id)
                    own_attempt_id = str(attempt.public_id)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            response = await client.get(
                "/api/v1/documents", params={"search": "Statistics", "limit": 1, "sort": "oldest"}
            )
            assert response.status_code == 200, response.text
            page = response.json()
            assert [item["id"] for item in page["items"]] == [str(first)]
            assert page["next_cursor"]
            following = await client.get(
                "/api/v1/documents",
                params={
                    "search": "Statistics",
                    "limit": 1,
                    "sort": "oldest",
                    "cursor": page["next_cursor"],
                },
            )
            assert [item["id"] for item in following.json()["items"]] == [str(literal)]
            newest = await client.get("/api/v1/documents", params={"search": "Statistics"})
            assert [item["id"] for item in newest.json()["items"]] == [str(literal), str(first)]
            escaped = await client.get("/api/v1/documents", params={"search": "_100%"})
            assert [item["id"] for item in escaped.json()["items"]] == [str(literal)]
            processing = await client.get("/api/v1/documents", params={"status": "processing"})
            assert [item["id"] for item in processing.json()["items"]] == [str(pending)]
            collection_response = await client.post(
                "/api/v1/collections",
                json={"name": "Batch fixture", "document_ids": [str(first), str(literal)]},
            )
            assert collection_response.status_code == 201, collection_response.text
            collection = collection_response.json()
            assert set(collection["document_ids"]) == {str(first), str(literal)}
            filtered = await client.get(
                "/api/v1/documents", params={"collection_id": collection["id"], "search": "_100%"}
            )
            assert [item["id"] for item in filtered.json()["items"]] == [str(literal)]
            foreign_collection = await store.create_collection(
                foreign.public_id, "foreign collection", "", [other]
            )
            hidden = await client.get(
                "/api/v1/documents", params={"collection_id": foreign_collection["id"]}
            )
            assert hidden.json()["items"] == []
            for identifiers, status in (
                ([str(first), str(other)], 404),
                ([str(removed)], 404),
                ([str(first), str(first)], 422),
            ):
                failed = await client.post(
                    "/api/v1/collections",
                    json={"name": "Must not persist", "document_ids": identifiers},
                )
                assert failed.status_code == status, failed.text
            async with sessions.begin() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Collection)
                        .where(Collection.workspace_id == owner.id)
                    )
                    == 1
                )
            resume = await client.get("/api/v1/learning/resume")
            assert resume.status_code == 200, resume.text
            assert resume.json() == {
                "conversation": {
                    "id": str(active.public_id),
                    "title": "Active task",
                    "working": True,
                },
                "attempt": {
                    "id": own_attempt_id,
                    "quiz_id": own_quiz_id,
                    "title": "Unfinished practice",
                    "status": "in_progress",
                },
            }
            async with sessions.begin() as session:
                await session.execute(
                    update(ConversationMessage)
                    .where(ConversationMessage.workspace_id == owner.id)
                    .values(status="cancelled")
                )
            resumed = await client.get("/api/v1/learning/resume")
            assert resumed.json()["conversation"] == {
                "id": str(recent.public_id),
                "title": "New conversation",
                "working": False,
            }
            app.dependency_overrides[get_current_principal] = lambda: Principal(uuid4())
            missing_workspace = await client.get("/api/v1/learning/resume")
            assert missing_workspace.status_code == 404
            app.dependency_overrides.pop(get_current_principal)
            assert (await client.get("/api/v1/learning/resume")).status_code == 401
        print(
            "PASS: scoped search, literal search, both cursor orders, collection filters, "
            "atomic batch creation, resume isolation and authentication"
        )
    finally:
        app.dependency_overrides.pop(get_current_principal, None)
        app.dependency_overrides.pop(get_learning_service, None)
        try:
            async with sessions.begin() as session:
                workspace_ids = select(WorkspaceModel.id).where(
                    WorkspaceModel.public_id.in_(owners)
                )
                await session.execute(
                    update(DocumentModel)
                    .where(DocumentModel.workspace_id.in_(workspace_ids))
                    .values(active_version_id=None)
                )
                await session.execute(
                    delete(DocumentModel).where(DocumentModel.workspace_id.in_(workspace_ids))
                )
                await session.execute(
                    delete(WorkspaceModel).where(WorkspaceModel.public_id.in_(owners))
                )
        finally:
            await close_database()


if __name__ == "__main__":
    asyncio.run(verify())
