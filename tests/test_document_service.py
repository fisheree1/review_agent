from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApplicationError
from app.documents.application.service import DocumentService
from app.documents.domain.entities import (
    CitationLocator,
    CitationLocatorKind,
    Document,
    DocumentContent,
    DocumentContentLocation,
    DocumentStatus,
)


class MemoryUpload:
    filename = "notes.pdf"
    content_type = "application/pdf"

    def __init__(self) -> None:
        self._content = BytesIO(b"%PDF-test")

    async def read(self, size: int = -1) -> bytes:
        return self._content.read(size)


class RecordingStorage:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def upload(self, *, object_key: str, source_path: Path, length: int) -> None:
        assert length == 9
        self.events.append("storage.upload")

    async def delete(self, *, object_key: str) -> None:
        self.events.append("storage.delete")

    async def download(self, *, object_key: str, destination_path: Path) -> None:
        raise AssertionError("download is not part of upload")


def make_document(
    workspace_id: UUID,
    *,
    status: DocumentStatus,
    page_count: int | None = None,
) -> Document:
    timestamp = datetime(2026, 9, 21, tzinfo=UTC)
    return Document(
        public_id=uuid4(),
        workspace_public_id=workspace_id,
        original_filename="notes.pdf",
        media_type="application/pdf",
        byte_size=9,
        sha256=hashlib.sha256(b"%PDF-test").hexdigest(),
        status=status,
        page_count=page_count,
        failure_code=None,
        failure_message=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


class UploadRepository:
    def __init__(
        self,
        *,
        events: list[str],
        workspace_public_id: UUID,
        existing: Document | None = None,
    ) -> None:
        self.events = events
        self.workspace_public_id = workspace_public_id
        self.existing = existing
        self.created: Document | None = None
        self.requested_content: list[tuple[int, int]] = []

    async def find_by_upload_key(
        self, *, workspace_public_id: UUID, upload_key: str
    ) -> Document | None:
        return self.existing

    async def find_by_hash(self, *, workspace_public_id: UUID, sha256: str) -> Document | None:
        return self.existing

    async def ensure_workspace(self, workspace_public_id: UUID) -> int:
        return 1

    async def create_document(
        self,
        *,
        workspace_id: int,
        workspace_public_id: UUID,
        public_id: UUID,
        original_filename: str,
        media_type: str,
        byte_size: int,
        sha256: str,
        object_key: str,
        upload_key: str,
    ) -> Document:
        self.events.append("database.create")
        self.created = make_document(workspace_public_id, status=DocumentStatus.UPLOADED)
        self.created = Document(
            public_id=public_id,
            workspace_public_id=self.created.workspace_public_id,
            original_filename=original_filename,
            media_type=media_type,
            byte_size=byte_size,
            sha256=sha256,
            status=self.created.status,
            page_count=None,
            failure_code=None,
            failure_message=None,
            created_at=self.created.created_at,
            updated_at=self.created.updated_at,
        )
        return self.created

    async def queue_initial_processing(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        job_public_id: UUID,
    ) -> Document:
        self.events.append("database.queue")
        source = self.created or self.existing
        assert source is not None
        return Document(
            public_id=document_public_id,
            workspace_public_id=workspace_public_id,
            original_filename=source.original_filename,
            media_type=source.media_type,
            byte_size=source.byte_size,
            sha256=source.sha256,
            status=DocumentStatus.QUEUED,
            page_count=None,
            failure_code=None,
            failure_message=None,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )

    async def mark_upload_failed(self, **_: object) -> None:
        self.events.append("database.fail")

    async def get_document(
        self, *, workspace_public_id: UUID, document_public_id: UUID
    ) -> Document | None:
        if self.existing is None or self.existing.public_id != document_public_id:
            return None
        return self.existing

    async def list_content(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        start_ordinal: int,
        limit: int,
        version_id: int | None = None,
    ) -> tuple[DocumentContent, ...]:
        self.requested_content.append((start_ordinal, limit))
        return (
            DocumentContent(
                ordinal=start_ordinal,
                content=f"Page {start_ordinal}",
                locator=CitationLocator(
                    kind=CitationLocatorKind.PAGE,
                    position=start_ordinal,
                ),
            ),
        )

    async def list_content_locations(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        version_id: int | None = None,
    ) -> tuple[DocumentContentLocation, ...]:
        return (
            DocumentContentLocation(
                ordinal=1,
                locator=CitationLocator(kind=CitationLocatorKind.PAGE, position=1),
            ),
        )


class UploadUnitOfWork:
    def __init__(
        self,
        repository: UploadRepository,
        events: list[str],
        *,
        fail_commit: bool,
    ) -> None:
        self.documents = repository
        self.events = events
        self.fail_commit = fail_commit

    async def __aenter__(self) -> UploadUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self.events.append("database.commit")
        if self.fail_commit:
            raise RuntimeError("database unavailable")


def make_service(
    *,
    events: list[str],
    repository: UploadRepository,
    fail_commit: bool = False,
) -> DocumentService:
    return DocumentService(
        unit_of_work_factory=lambda: UploadUnitOfWork(
            repository,
            events,
            fail_commit=fail_commit,
        ),  # type: ignore[arg-type]
        storage=RecordingStorage(events),
        max_upload_bytes=1024,
    )


@pytest.mark.anyio
async def test_new_upload_creates_document_and_job_in_one_commit_after_storage() -> None:
    events: list[str] = []
    workspace_id = uuid4()
    repository = UploadRepository(events=events, workspace_public_id=workspace_id)

    result = await make_service(events=events, repository=repository).upload(
        workspace_public_id=workspace_id,
        source=MemoryUpload(),
        idempotency_key="atomic-upload",
    )

    assert result.document.status == DocumentStatus.QUEUED
    assert events == ["storage.upload", "database.create", "database.queue", "database.commit"]


@pytest.mark.anyio
async def test_database_failure_compensates_uploaded_object() -> None:
    events: list[str] = []
    workspace_id = uuid4()
    repository = UploadRepository(events=events, workspace_public_id=workspace_id)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await make_service(events=events, repository=repository, fail_commit=True).upload(
            workspace_public_id=workspace_id,
            source=MemoryUpload(),
            idempotency_key="failed-commit",
        )

    assert events[-1] == "storage.delete"


@pytest.mark.anyio
async def test_reupload_resumes_legacy_uploaded_document() -> None:
    events: list[str] = []
    workspace_id = uuid4()
    existing = make_document(workspace_id, status=DocumentStatus.UPLOADED)
    repository = UploadRepository(
        events=events,
        workspace_public_id=workspace_id,
        existing=existing,
    )

    result = await make_service(events=events, repository=repository).upload(
        workspace_public_id=workspace_id,
        source=MemoryUpload(),
        idempotency_key="resume-upload",
    )

    assert result.deduplicated is True
    assert result.document.status == DocumentStatus.QUEUED
    assert events == ["storage.upload", "database.queue", "database.commit"]


@pytest.mark.anyio
async def test_reader_fetches_only_the_requested_page() -> None:
    events: list[str] = []
    workspace_id = uuid4()
    existing = make_document(workspace_id, status=DocumentStatus.READY, page_count=120)
    repository = UploadRepository(
        events=events,
        workspace_public_id=workspace_id,
        existing=existing,
    )

    result = await make_service(events=events, repository=repository).content(
        workspace_public_id=workspace_id,
        document_public_id=existing.public_id,
        ordinal=57,
    )

    assert result.content_count == 120
    assert result.contents[0].ordinal == 57
    assert result.contents[0].locator.position == 57
    assert repository.requested_content == [(57, 1)]


@pytest.mark.anyio
async def test_reader_rejects_a_page_outside_the_document() -> None:
    events: list[str] = []
    workspace_id = uuid4()
    existing = make_document(workspace_id, status=DocumentStatus.READY, page_count=2)
    repository = UploadRepository(
        events=events,
        workspace_public_id=workspace_id,
        existing=existing,
    )

    with pytest.raises(ApplicationError) as captured:
        await make_service(events=events, repository=repository).content(
            workspace_public_id=workspace_id,
            document_public_id=existing.public_id,
            ordinal=3,
        )

    assert captured.value.code == "CONTENT_NOT_FOUND"
    assert repository.requested_content == []
