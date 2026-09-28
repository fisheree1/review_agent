from __future__ import annotations

import asyncio
from pathlib import Path
from types import TracebackType
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from app.core.auth import Principal, get_current_principal
from app.documents.api import get_document_service
from app.documents.application.errors import StorageOperationError
from app.documents.application.service import DocumentService
from app.documents.domain.entities import DocumentSource, DocumentStatus
from app.documents.infrastructure.repository import SqlAlchemyDocumentRepository
from app.main import app


class SourceRepository:
    def __init__(self, *, owner: UUID, document_id: UUID, source: DocumentSource) -> None:
        self.owner = owner
        self.document_id = document_id
        self.source = source

    async def get_document_source(
        self, *, workspace_public_id: UUID, document_public_id: UUID
    ) -> DocumentSource | None:
        if workspace_public_id != self.owner or document_public_id != self.document_id:
            return None
        return self.source


class SourceUnitOfWork:
    def __init__(self, repository: SourceRepository) -> None:
        self.documents = repository

    async def __aenter__(self) -> SourceUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class SourceStorage:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.downloads = 0
        self.paths: list[Path] = []

    async def download(self, *, object_key: str, destination_path: Path) -> None:
        self.downloads += 1
        self.paths.append(destination_path)
        assert object_key == "private/document/source.pdf"
        await asyncio.to_thread(destination_path.write_bytes, b"%PDF-1.4\npreview")
        if self.unavailable:
            raise StorageOperationError("private detail")


@pytest.mark.anyio
async def test_source_lookup_requires_workspace_and_document_scope() -> None:
    owner = uuid4()
    document_id = uuid4()

    class CapturingSession:
        async def execute(self, statement: object) -> object:
            compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
            sql = str(compiled)
            assert "workspaces.public_id =" in sql
            assert "documents.public_id =" in sql
            assert "documents.deleted_at IS NULL" in sql
            assert owner in compiled.params.values()
            assert document_id in compiled.params.values()

            class EmptyResult:
                def one_or_none(self) -> None:
                    return None

            return EmptyResult()

    repository = SqlAlchemyDocumentRepository(CapturingSession())  # type: ignore[arg-type]
    assert (
        await repository.get_document_source(
            workspace_public_id=owner, document_public_id=document_id
        )
        is None
    )


def preview_service(
    owner: UUID,
    document_id: UUID,
    *,
    media_type: str = "application/pdf",
    status: DocumentStatus = DocumentStatus.READY,
    unavailable: bool = False,
) -> tuple[DocumentService, SourceStorage]:
    repository = SourceRepository(
        owner=owner,
        document_id=document_id,
        source=DocumentSource(
            object_key="private/document/source.pdf", media_type=media_type, status=status
        ),
    )
    storage = SourceStorage(unavailable=unavailable)
    service = DocumentService(
        unit_of_work_factory=lambda: SourceUnitOfWork(repository),  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        max_upload_bytes=1024,
    )
    return service, storage


async def request_preview(
    *, principal_workspace: UUID, service: DocumentService, document_id: UUID
) -> httpx.Response:
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_principal] = lambda: Principal(principal_workspace)
    app.dependency_overrides[get_document_service] = lambda: service
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get(f"/api/v1/documents/{document_id}/original")
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.anyio
async def test_pdf_preview_returns_private_original_after_workspace_authorization() -> None:
    owner = uuid4()
    document_id = uuid4()
    service, storage = preview_service(owner, document_id)

    response = await request_preview(
        principal_workspace=owner, service=service, document_id=document_id
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4\npreview"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == 'inline; filename="document.pdf"'
    assert storage.downloads == 1
    assert not storage.paths[0].exists()


@pytest.mark.anyio
async def test_pdf_preview_denies_another_workspace_without_downloading() -> None:
    document_id = uuid4()
    service, storage = preview_service(uuid4(), document_id)

    response = await request_preview(
        principal_workspace=uuid4(), service=service, document_id=document_id
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
    assert storage.downloads == 0


@pytest.mark.anyio
async def test_pdf_preview_requires_authentication() -> None:
    owner = uuid4()
    document_id = uuid4()
    service, storage = preview_service(owner, document_id)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.pop(get_current_principal, None)
    app.dependency_overrides[get_document_service] = lambda: service
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/documents/{document_id}/original")
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    assert response.status_code == 401
    assert storage.downloads == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("media_type", "status", "expected_status", "code"),
    [
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            DocumentStatus.READY,
            415,
            "PDF_PREVIEW_UNSUPPORTED",
        ),
        ("application/pdf", DocumentStatus.DELETING, 409, "DOCUMENT_NOT_READY"),
    ],
)
async def test_pdf_preview_rejects_unsupported_or_unready_document(
    media_type: str, status: DocumentStatus, expected_status: int, code: str
) -> None:
    owner = uuid4()
    document_id = uuid4()
    service, storage = preview_service(owner, document_id, media_type=media_type, status=status)

    response = await request_preview(
        principal_workspace=owner, service=service, document_id=document_id
    )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == code
    assert storage.downloads == 0


@pytest.mark.anyio
@pytest.mark.parametrize("status", [DocumentStatus.QUEUED, DocumentStatus.FAILED])
async def test_pdf_preview_is_available_before_or_after_parsing_failure(
    status: DocumentStatus,
) -> None:
    owner = uuid4()
    document_id = uuid4()
    service, storage = preview_service(owner, document_id, status=status)

    response = await request_preview(
        principal_workspace=owner, service=service, document_id=document_id
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert storage.downloads == 1


@pytest.mark.anyio
async def test_pdf_preview_storage_failure_has_stable_error() -> None:
    owner = uuid4()
    document_id = uuid4()
    service, storage = preview_service(owner, document_id, unavailable=True)

    response = await request_preview(
        principal_workspace=owner, service=service, document_id=document_id
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert "private detail" not in response.text
    assert not storage.paths[0].exists()
