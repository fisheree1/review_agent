from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from app.documents.domain.entities import (
    ClaimedJob,
    DeleteTarget,
    Document,
    DocumentContent,
    DocumentContentLocation,
    DocumentListPage,
    DocumentListPosition,
    ParsedDocument,
)


class UploadSource(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...


class DocumentStorage(Protocol):
    async def upload(self, *, object_key: str, source_path: Path, length: int) -> None: ...

    async def download(self, *, object_key: str, destination_path: Path) -> None: ...

    async def delete(self, *, object_key: str) -> None: ...


class DocumentParser(Protocol):
    async def parse(self, source_path: Path, *, media_type: str) -> ParsedDocument: ...


class DocumentRepository(Protocol):
    async def ensure_workspace(self, workspace_public_id: UUID) -> int: ...

    async def find_by_upload_key(
        self, *, workspace_public_id: UUID, upload_key: str
    ) -> Document | None: ...

    async def find_by_hash(self, *, workspace_public_id: UUID, sha256: str) -> Document | None: ...

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
    ) -> Document: ...

    async def queue_initial_processing(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        job_public_id: UUID,
    ) -> Document: ...

    async def mark_upload_failed(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        failure_code: str,
        failure_message: str,
    ) -> None: ...

    async def get_document(
        self, *, workspace_public_id: UUID, document_public_id: UUID
    ) -> Document | None: ...

    async def list_documents(
        self,
        *,
        workspace_public_id: UUID,
        limit: int,
        after: DocumentListPosition | None,
    ) -> DocumentListPage: ...

    async def list_content(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        start_ordinal: int,
        limit: int,
        version_id: int | None = None,
    ) -> tuple[DocumentContent, ...]: ...

    async def list_content_locations(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        version_id: int | None = None,
    ) -> tuple[DocumentContentLocation, ...]: ...

    async def queue_retry(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        retry_key: str,
        job_public_id: UUID,
    ) -> Document: ...

    async def prepare_delete(
        self, *, workspace_public_id: UUID, document_public_id: UUID
    ) -> DeleteTarget | None: ...

    async def mark_delete_failed(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        failure_code: str,
        failure_message: str,
    ) -> None: ...

    async def complete_delete(
        self, *, workspace_public_id: UUID, document_public_id: UUID
    ) -> None: ...

    async def claim_next_job(self, *, lease_seconds: int) -> ClaimedJob | None: ...

    async def complete_job(self, *, job: ClaimedJob, parsed: ParsedDocument) -> None: ...

    async def fail_job(
        self,
        *,
        job: ClaimedJob,
        failure_code: str,
        failure_message: str,
    ) -> None: ...


class DocumentsUnitOfWork(Protocol):
    documents: DocumentRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...


UnitOfWorkFactory = Callable[[], DocumentsUnitOfWork]


class DuplicateDocumentError(Exception):
    pass
