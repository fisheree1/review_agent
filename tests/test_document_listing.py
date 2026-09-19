from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApplicationError
from app.documents.application.service import DocumentService
from app.documents.domain.entities import (
    Document,
    DocumentListPage,
    DocumentListPosition,
    DocumentStatus,
)


class ListingRepository:
    def __init__(self, page: DocumentListPage) -> None:
        self.page = page
        self.workspace_public_id: UUID | None = None
        self.after: DocumentListPosition | None = None

    async def list_documents(
        self,
        *,
        workspace_public_id: UUID,
        limit: int,
        after: DocumentListPosition | None,
    ) -> DocumentListPage:
        assert limit == 30
        self.workspace_public_id = workspace_public_id
        self.after = after
        return self.page


class ListingUnitOfWork:
    def __init__(self, repository: ListingRepository) -> None:
        self.documents = repository

    async def __aenter__(self) -> ListingUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None


class UnusedStorage:
    pass


def document(workspace_public_id: UUID) -> Document:
    timestamp = datetime(2026, 9, 19, 2, 0, tzinfo=UTC)
    return Document(
        public_id=uuid4(),
        workspace_public_id=workspace_public_id,
        original_filename="Data profiling.pdf",
        media_type="application/pdf",
        byte_size=1024,
        sha256="a" * 64,
        status=DocumentStatus.READY,
        page_count=4,
        failure_code=None,
        failure_message=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.anyio
async def test_document_list_cursor_keeps_workspace_scope() -> None:
    workspace_public_id = uuid4()
    position = DocumentListPosition(
        created_at=datetime(2026, 9, 19, 2, 0, tzinfo=UTC),
        internal_id=42,
    )
    repository = ListingRepository(
        DocumentListPage(items=(document(workspace_public_id),), next_position=position)
    )
    service = DocumentService(
        unit_of_work_factory=lambda: ListingUnitOfWork(repository),  # type: ignore[arg-type]
        storage=UnusedStorage(),  # type: ignore[arg-type]
        max_upload_bytes=1024,
    )

    first_page = await service.list(
        workspace_public_id=workspace_public_id,
        limit=30,
        cursor=None,
    )
    second_page = await service.list(
        workspace_public_id=workspace_public_id,
        limit=30,
        cursor=first_page.next_cursor,
    )

    assert repository.workspace_public_id == workspace_public_id
    assert repository.after == position
    assert second_page.items[0].workspace_public_id == workspace_public_id


@pytest.mark.anyio
async def test_document_list_rejects_invalid_cursor() -> None:
    repository = ListingRepository(DocumentListPage(items=(), next_position=None))
    service = DocumentService(
        unit_of_work_factory=lambda: ListingUnitOfWork(repository),  # type: ignore[arg-type]
        storage=UnusedStorage(),  # type: ignore[arg-type]
        max_upload_bytes=1024,
    )

    with pytest.raises(ApplicationError) as error:
        await service.list(workspace_public_id=uuid4(), limit=30, cursor="not-a-cursor")

    assert error.value.code == "INVALID_CURSOR"
