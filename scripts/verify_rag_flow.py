"""Risk-focused integration checks on an isolated, migrated PostgreSQL database.

Uses synthetic documents and fake providers; never sends text to cloud services.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.auth import Principal, get_current_principal
from app.core.database import async_session_factory as sessions
from app.core.database import close_database
from app.core.errors import ApplicationError
from app.documents.infrastructure.models import (
    DocumentModel,
    DocumentPageModel,
    DocumentVersionModel,
    WorkspaceModel,
)
from app.main import app
from app.rag.api import get_rag_service
from app.rag.application import RagProcessor, RagService
from app.rag.domain import Evidence, RagFailure, Scope
from app.rag.models import DocumentChunk, DocumentIndex, RagQuestion
from app.rag.store import SqlRagStore

PROFILE = "integration-fake:1024:source-window-1500-180-v1"


class FakeModels:
    def __init__(self) -> None:
        self.embedded = 0
        self.fail = False

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if self.fail:
            raise RagFailure("PROVIDER_LIMIT", "synthetic rate limit")
        if not query:
            self.embedded += len(texts)
        return [[1.0] + [0.0] * 1023 for _ in texts]

    async def answer(
        self, question: str, sources: list[Evidence]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        source = sources[0]
        return {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "The median resists outliers.",
                    "citations": [{"source_id": str(source.id), "quote": source.content[:80]}],
                }
            ],
        }, {}


async def seed(workspace: UUID, *, long: bool = False) -> tuple[Scope, int, int]:
    async with sessions.begin() as session:
        owner = WorkspaceModel(public_id=workspace, name="RAG integration fixture", status="active")
        session.add(owner)
        await session.flush()
        document = DocumentModel(
            workspace_id=owner.id,
            original_filename="synthetic.pdf",
            media_type="application/pdf",
            byte_size=100,
            sha256=uuid4().hex * 2,
            object_key=f"rag-test/{uuid4()}",
            upload_idempotency_key=str(uuid4()),
            status="ready",
        )
        session.add(document)
        await session.flush()
        version = DocumentVersionModel(
            document_id=document.id,
            workspace_id=owner.id,
            version_no=1,
            source_sha256=document.sha256,
            parser_name="fixture",
            parser_version="1",
            status="ready",
            page_count=2,
        )
        session.add(version)
        await session.flush()
        document.active_version_id = version.id
        source = "The median is robust against extreme outliers. "
        for unit in (1, 2):
            content = source * (600 if long and unit == 1 else 1)
            session.add(
                DocumentPageModel(
                    workspace_id=owner.id,
                    document_version_id=version.id,
                    page_number=unit,
                    content=content,
                    char_count=len(content),
                    locator_kind="page",
                    locator_position=unit,
                    locator_path=[],
                )
            )
        return Scope(workspace, document.public_id), owner.id, version.id


async def verify() -> None:
    owners: list[int] = []
    try:
        scope, owner, version = await seed(uuid4(), long=True)
        owners.append(owner)
        foreign, other_owner, other_version = await seed(uuid4())
        owners.append(other_owner)
        store = SqlRagStore(sessions, PROFILE)
        models = FakeModels()
        processor = RagProcessor(store, models, models)

        assert (await store.index(scope))["status"] == "not_indexed"
        await store.index(scope, "first")
        await store.index(scope, "first")
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(DocumentIndex)
                    .where(DocumentIndex.workspace_id == owner)
                )
                == 1
            )
        await processor.process_index()
        progress = await store.index(scope)
        assert progress["completed"] == 16 and progress["status"] == "queued"
        try:
            await store.ask(scope, "too-early", "Why median?")
            raise AssertionError("Partial index was exposed")
        except ApplicationError as exc:
            assert exc.code == "INDEX_NOT_READY"
        models.fail = True
        await processor.process_index()
        assert (await store.index(scope))["status"] == "failed"
        assert (await store.index(scope, "first"))["status"] == "failed"
        await store.index(scope, "retry")
        models.fail = False
        for _ in range(8):
            await processor.process_index()
            if (await store.index(scope))["status"] == "ready":
                break
        final = await store.index(scope)
        assert final["status"] == "ready" and models.embedded == final["total"]
        print("PASS: partial indexes hidden; duplicate requests and failed batches reuse vectors")

        await store.index(foreign, "foreign-index")
        await processor.process_index()
        question = await store.ask(scope, "question-key", "Why median?")
        assert (await store.ask(scope, "question-key", "Why median?"))["id"] == question["id"]
        task = await store.claim_question()
        assert task is not None
        sources = await store.retrieve(task, [1.0] + [0.0] * 1023)
        async with sessions() as session:
            permitted = set(
                await session.scalars(
                    select(DocumentChunk.public_id).where(DocumentChunk.workspace_id == owner)
                )
            )
        assert sources and all(source.id in permitted for source in sources)
        await store.cancel(scope, UUID(question["id"]))
        await store.finish_question(task, {"insufficient_evidence": True, "claims": []}, {})
        assert (await store.question(scope, UUID(question["id"])))["status"] == "cancelled"
        print("PASS: retrieval scoped before generation; cancellation fences late publication")

        question = await store.ask(scope, "new-question-key", "Explain outliers")
        await processor.process_question()
        result = await store.question(scope, UUID(question["id"]))
        assert result["status"] == "answered"
        assert result["answer"]["claims"][0]["citations"][0]["unit"] in (1, 2)
        try:
            await store.question(Scope(foreign.workspace, scope.document), UUID(question["id"]))
            raise AssertionError("Workspace isolation failed")
        except ApplicationError as exc:
            assert exc.status_code == 404

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            base = f"/api/v1/documents/{scope.document}"
            assert (await client.get(f"{base}/index")).status_code == 401
            app.dependency_overrides[get_current_principal] = lambda: Principal(scope.workspace)
            app.dependency_overrides[get_rag_service] = lambda: RagService(store)
            assert (await client.get(f"{base}/index")).status_code == 200
            assert (
                await client.post(
                    f"{base}/questions", headers={"Idempotency-Key": "bad"}, json={"question": " "}
                )
            ).status_code == 422
            assert (
                await client.post(
                    f"{base}/questions",
                    headers={"Idempotency-Key": "oversize"},
                    content=b"x" * 17000,
                )
            ).status_code == 413
            assert (
                await client.get(f"/api/v1/documents/{foreign.document}/index")
            ).status_code == 404
            response = await client.post(
                f"{base}/questions",
                headers={"Idempotency-Key": "api"},
                json={"question": "Explain outliers"},
            )
            assert response.status_code == 202, response.status_code
        print("PASS: API success, authentication, invalid input, body limits, workspace isolation")

        try:
            async with sessions.begin() as session:
                session.add(
                    DocumentIndex(
                        workspace_id=other_owner,
                        document_version_id=version,
                        profile="wrong-owner",
                        retry_key="bad",
                    )
                )
            raise AssertionError("Database accepted cross-workspace version")
        except IntegrityError:
            pass
        async with sessions.begin() as session:
            await session.execute(
                update(RagQuestion)
                .where(RagQuestion.workspace_id == owner, RagQuestion.status == "queued")
                .values(
                    status="processing",
                    fence=uuid4(),
                    lease_until=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
        assert await store.claim_question() is None
        assert any(q["failure_code"] == "WORKER_INTERRUPTED" for q in await store.history(scope))
        print(
            "PASS: database rejects cross-workspace writes; "
            "interrupted generation fails without silent rebilling"
        )

        async with sessions.begin() as session:
            await session.execute(
                update(DocumentModel)
                .where(DocumentModel.workspace_id == owner)
                .values(status="deleting")
            )
        assert await store.retrieve(task, [1.0] + [0.0] * 1023) == []
        async with sessions.begin() as session:
            await session.execute(
                update(DocumentModel)
                .where(DocumentModel.workspace_id == owner)
                .values(active_version_id=None)
            )
            await session.execute(
                delete(DocumentVersionModel).where(DocumentVersionModel.id == version)
            )
        async with sessions() as session:
            for table in (DocumentIndex, DocumentChunk, RagQuestion):
                assert (
                    await session.scalar(
                        select(func.count()).select_from(table).where(table.workspace_id == owner)
                    )
                    == 0
                )
            plan = await session.execute(
                text(
                    "EXPLAIN SELECT * FROM review_agent.document_chunks "
                    "WHERE workspace_id=:owner AND index_id=-1 "
                    "ORDER BY embedding <=> CAST(:vector AS vector) LIMIT 6"
                ),
                {"owner": other_owner, "vector": "[" + ",".join(["1"] + ["0"] * 1023) + "]"},
            )
            print(
                "Retrieval plan:",
                " | ".join(row[0].split("Sort Key:")[0].strip() for row in plan),
            )
        print(
            "PASS: deletion excludes retrieval and cascades vectors/questions; constraints verified"
        )
    finally:
        app.dependency_overrides.clear()
        async with sessions.begin() as session:
            await session.execute(
                update(DocumentModel)
                .where(DocumentModel.workspace_id.in_(owners))
                .values(active_version_id=None)
            )
            await session.execute(
                delete(DocumentModel).where(DocumentModel.workspace_id.in_(owners))
            )
            await session.execute(delete(WorkspaceModel).where(WorkspaceModel.id.in_(owners)))
        await close_database()


if __name__ == "__main__":
    asyncio.run(verify())
