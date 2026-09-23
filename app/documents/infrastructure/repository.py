from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.documents.application.ports import DocumentRepository, DuplicateDocumentError
from app.documents.domain.entities import (
    CitationLocator,
    CitationLocatorKind,
    ClaimedJob,
    DeleteTarget,
    Document,
    DocumentContent,
    DocumentContentLocation,
    DocumentListPage,
    DocumentListPosition,
    DocumentStatus,
    JobStatus,
    ParsedDocument,
)
from app.documents.infrastructure.models import (
    DocumentModel,
    DocumentPageModel,
    DocumentVersionModel,
    ProcessingJobModel,
    WorkspaceModel,
)


def _document_view(model: DocumentModel, workspace_public_id: UUID) -> Document:
    return Document(
        public_id=model.public_id,
        workspace_public_id=workspace_public_id,
        original_filename=model.original_filename,
        media_type=model.media_type,
        byte_size=model.byte_size,
        sha256=model.sha256,
        status=DocumentStatus(model.status),
        page_count=model.page_count,
        failure_code=model.failure_code,
        failure_message=model.failure_message,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyDocumentRepository(DocumentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_workspace(self, workspace_public_id: UUID) -> int:
        statement = (
            insert(WorkspaceModel)
            .values(public_id=workspace_public_id, name="Personal Workspace", status="active")
            .on_conflict_do_nothing(index_elements=[WorkspaceModel.public_id])
        )
        await self._session.execute(statement)
        workspace_id = await self._session.scalar(
            select(WorkspaceModel.id).where(
                WorkspaceModel.public_id == workspace_public_id,
                WorkspaceModel.status == "active",
            )
        )
        if workspace_id is None:
            raise RuntimeError("Configured workspace is not active")
        return workspace_id

    async def find_by_upload_key(
        self, *, workspace_public_id: UUID, upload_key: str
    ) -> Document | None:
        row = (
            await self._session.execute(
                select(DocumentModel, WorkspaceModel.public_id)
                .join(WorkspaceModel, WorkspaceModel.id == DocumentModel.workspace_id)
                .where(
                    WorkspaceModel.public_id == workspace_public_id,
                    DocumentModel.upload_idempotency_key == upload_key,
                    DocumentModel.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        return None if row is None else _document_view(row[0], row[1])

    async def find_by_hash(self, *, workspace_public_id: UUID, sha256: str) -> Document | None:
        row = (
            await self._session.execute(
                select(DocumentModel, WorkspaceModel.public_id)
                .join(WorkspaceModel, WorkspaceModel.id == DocumentModel.workspace_id)
                .where(
                    WorkspaceModel.public_id == workspace_public_id,
                    DocumentModel.sha256 == sha256,
                    DocumentModel.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        return None if row is None else _document_view(row[0], row[1])

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
        model = DocumentModel(
            public_id=public_id,
            workspace_id=workspace_id,
            original_filename=original_filename,
            media_type=media_type,
            byte_size=byte_size,
            sha256=sha256,
            object_key=object_key,
            upload_idempotency_key=upload_key,
            status=DocumentStatus.UPLOADED.value,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise DuplicateDocumentError from exc
        return _document_view(model, workspace_public_id)

    async def _locked_document(
        self, *, workspace_public_id: UUID, document_public_id: UUID
    ) -> tuple[DocumentModel, int] | None:
        row = (
            await self._session.execute(
                select(DocumentModel, WorkspaceModel.id)
                .join(WorkspaceModel, WorkspaceModel.id == DocumentModel.workspace_id)
                .where(
                    WorkspaceModel.public_id == workspace_public_id,
                    DocumentModel.public_id == document_public_id,
                )
                .with_for_update(of=DocumentModel)
            )
        ).one_or_none()
        return None if row is None else (row[0], row[1])

    async def queue_initial_processing(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        job_public_id: UUID,
    ) -> Document:
        locked = await self._locked_document(
            workspace_public_id=workspace_public_id,
            document_public_id=document_public_id,
        )
        if locked is None:
            raise RuntimeError("Document disappeared before it could be queued")
        document, workspace_id = locked
        idempotency_key = f"parse:{document_public_id}:initial"
        existing_job = await self._session.scalar(
            select(ProcessingJobModel.id).where(
                ProcessingJobModel.workspace_id == workspace_id,
                ProcessingJobModel.job_type == "document_parse",
                ProcessingJobModel.idempotency_key == idempotency_key,
            )
        )
        if existing_job is None:
            self._session.add(
                ProcessingJobModel(
                    public_id=job_public_id,
                    workspace_id=workspace_id,
                    document_id=document.id,
                    job_type="document_parse",
                    idempotency_key=idempotency_key,
                    status=JobStatus.QUEUED.value,
                )
            )
        if document.status in {
            DocumentStatus.UPLOADED.value,
            DocumentStatus.FAILED.value,
        }:
            document.status = DocumentStatus.QUEUED.value
            document.failure_code = None
            document.failure_message = None
            document.updated_at = datetime.now(UTC)
        await self._session.flush()
        return _document_view(document, workspace_public_id)

    async def mark_upload_failed(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        failure_code: str,
        failure_message: str,
    ) -> None:
        locked = await self._locked_document(
            workspace_public_id=workspace_public_id,
            document_public_id=document_public_id,
        )
        if locked is None:
            return
        document, _ = locked
        document.status = DocumentStatus.FAILED.value
        document.failure_code = failure_code
        document.failure_message = failure_message
        document.updated_at = datetime.now(UTC)

    async def get_document(
        self, *, workspace_public_id: UUID, document_public_id: UUID
    ) -> Document | None:
        row = (
            await self._session.execute(
                select(DocumentModel, WorkspaceModel.public_id)
                .join(WorkspaceModel, WorkspaceModel.id == DocumentModel.workspace_id)
                .where(
                    WorkspaceModel.public_id == workspace_public_id,
                    DocumentModel.public_id == document_public_id,
                    DocumentModel.status != DocumentStatus.DELETED.value,
                )
            )
        ).one_or_none()
        return None if row is None else _document_view(row[0], row[1])

    async def list_documents(
        self,
        *,
        workspace_public_id: UUID,
        limit: int,
        after: DocumentListPosition | None,
    ) -> DocumentListPage:
        statement = (
            select(DocumentModel, WorkspaceModel.public_id)
            .join(WorkspaceModel, WorkspaceModel.id == DocumentModel.workspace_id)
            .where(
                WorkspaceModel.public_id == workspace_public_id,
                DocumentModel.status != DocumentStatus.DELETED.value,
            )
        )
        if after is not None:
            statement = statement.where(
                or_(
                    DocumentModel.created_at < after.created_at,
                    and_(
                        DocumentModel.created_at == after.created_at,
                        DocumentModel.id < after.internal_id,
                    ),
                )
            )
        rows = (
            await self._session.execute(
                statement.order_by(DocumentModel.created_at.desc(), DocumentModel.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
        visible_rows = rows[:limit]
        next_position = None
        if len(rows) > limit and visible_rows:
            last_document = visible_rows[-1][0]
            next_position = DocumentListPosition(
                created_at=last_document.created_at,
                internal_id=last_document.id,
            )
        return DocumentListPage(
            items=tuple(_document_view(row[0], row[1]) for row in visible_rows),
            next_position=next_position,
        )

    async def list_content(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        start_ordinal: int,
        limit: int,
        version_id: int | None = None,
    ) -> tuple[DocumentContent, ...]:
        rows = (
            await self._session.execute(
                select(
                    DocumentPageModel.page_number,
                    DocumentPageModel.content,
                    DocumentPageModel.locator_kind,
                    DocumentPageModel.locator_position,
                    DocumentPageModel.locator_title,
                    DocumentPageModel.locator_path,
                )
                .join(
                    DocumentVersionModel,
                    DocumentVersionModel.id == DocumentPageModel.document_version_id,
                )
                .join(DocumentModel, DocumentModel.id == DocumentVersionModel.document_id)
                .join(WorkspaceModel, WorkspaceModel.id == DocumentModel.workspace_id)
                .where(
                    WorkspaceModel.public_id == workspace_public_id,
                    DocumentModel.public_id == document_public_id,
                    DocumentModel.status == DocumentStatus.READY.value,
                    DocumentModel.deleted_at.is_(None),
                    DocumentVersionModel.id
                    == (version_id if version_id is not None else DocumentModel.active_version_id),
                    DocumentPageModel.workspace_id == WorkspaceModel.id,
                    DocumentPageModel.page_number >= start_ordinal,
                )
                .order_by(DocumentPageModel.page_number)
                .limit(limit)
            )
        ).all()
        return tuple(
            DocumentContent(
                ordinal=row[0],
                content=row[1],
                locator=CitationLocator(
                    kind=CitationLocatorKind(row[2]),
                    position=row[3],
                    title=row[4],
                    path=tuple(row[5]),
                ),
            )
            for row in rows
        )

    async def list_content_locations(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        version_id: int | None = None,
    ) -> tuple[DocumentContentLocation, ...]:
        rows = (
            await self._session.execute(
                select(
                    DocumentPageModel.page_number,
                    DocumentPageModel.locator_kind,
                    DocumentPageModel.locator_position,
                    DocumentPageModel.locator_title,
                    DocumentPageModel.locator_path,
                )
                .join(
                    DocumentVersionModel,
                    DocumentVersionModel.id == DocumentPageModel.document_version_id,
                )
                .join(DocumentModel, DocumentModel.id == DocumentVersionModel.document_id)
                .join(WorkspaceModel, WorkspaceModel.id == DocumentModel.workspace_id)
                .where(
                    WorkspaceModel.public_id == workspace_public_id,
                    DocumentModel.public_id == document_public_id,
                    DocumentModel.status == DocumentStatus.READY.value,
                    DocumentModel.deleted_at.is_(None),
                    DocumentVersionModel.id
                    == (version_id if version_id is not None else DocumentModel.active_version_id),
                    DocumentPageModel.workspace_id == WorkspaceModel.id,
                )
                .order_by(DocumentPageModel.page_number)
            )
        ).all()
        return tuple(
            DocumentContentLocation(
                ordinal=row[0],
                locator=CitationLocator(
                    kind=CitationLocatorKind(row[1]),
                    position=row[2],
                    title=row[3],
                    path=tuple(row[4]),
                ),
            )
            for row in rows
        )

    async def queue_retry(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        retry_key: str,
        job_public_id: UUID,
    ) -> Document:
        locked = await self._locked_document(
            workspace_public_id=workspace_public_id,
            document_public_id=document_public_id,
        )
        if locked is None:
            raise LookupError("document_not_found")
        document, workspace_id = locked
        idempotency_key = f"parse:{document_public_id}:retry:{retry_key}"
        existing_job = await self._session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.workspace_id == workspace_id,
                ProcessingJobModel.job_type == "document_parse",
                ProcessingJobModel.idempotency_key == idempotency_key,
            )
        )
        if existing_job is None:
            if document.status != DocumentStatus.FAILED.value:
                raise RuntimeError("document_not_failed")
            previous_job_id = await self._session.scalar(
                select(ProcessingJobModel.id)
                .where(ProcessingJobModel.document_id == document.id)
                .order_by(ProcessingJobModel.created_at.desc(), ProcessingJobModel.id.desc())
                .limit(1)
            )
            self._session.add(
                ProcessingJobModel(
                    public_id=job_public_id,
                    workspace_id=workspace_id,
                    document_id=document.id,
                    job_type="document_parse",
                    idempotency_key=idempotency_key,
                    status=JobStatus.QUEUED.value,
                    retry_of_job_id=previous_job_id,
                )
            )
            document.status = DocumentStatus.QUEUED.value
            document.failure_code = None
            document.failure_message = None
            document.updated_at = datetime.now(UTC)
            await self._session.flush()
        return _document_view(document, workspace_public_id)

    async def prepare_delete(
        self, *, workspace_public_id: UUID, document_public_id: UUID
    ) -> DeleteTarget | None:
        locked = await self._locked_document(
            workspace_public_id=workspace_public_id,
            document_public_id=document_public_id,
        )
        if locked is None:
            return None
        document, _ = locked
        view = _document_view(document, workspace_public_id)
        if document.status != DocumentStatus.DELETED.value:
            document.status = DocumentStatus.DELETING.value
            document.failure_code = None
            document.failure_message = None
            document.updated_at = datetime.now(UTC)
            await self._session.execute(
                update(ProcessingJobModel)
                .where(
                    ProcessingJobModel.document_id == document.id,
                    ProcessingJobModel.status.in_(
                        [JobStatus.QUEUED.value, JobStatus.PROCESSING.value]
                    ),
                )
                .values(
                    status=JobStatus.CANCELLED.value,
                    finished_at=datetime.now(UTC),
                    lease_expires_at=None,
                )
            )
        return DeleteTarget(document=view, object_key=document.object_key)

    async def mark_delete_failed(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        failure_code: str,
        failure_message: str,
    ) -> None:
        locked = await self._locked_document(
            workspace_public_id=workspace_public_id,
            document_public_id=document_public_id,
        )
        if locked is None:
            return
        document, _ = locked
        document.failure_code = failure_code
        document.failure_message = failure_message
        document.updated_at = datetime.now(UTC)

    async def complete_delete(self, *, workspace_public_id: UUID, document_public_id: UUID) -> None:
        locked = await self._locked_document(
            workspace_public_id=workspace_public_id,
            document_public_id=document_public_id,
        )
        if locked is None:
            return
        document, _ = locked
        if document.status == DocumentStatus.DELETED.value:
            return
        document.active_version_id = None
        await self._session.flush()
        await self._session.execute(
            delete(DocumentVersionModel).where(DocumentVersionModel.document_id == document.id)
        )
        document.status = DocumentStatus.DELETED.value
        document.page_count = None
        document.failure_code = None
        document.failure_message = None
        document.deleted_at = datetime.now(UTC)
        document.updated_at = datetime.now(UTC)

    async def claim_next_job(self, *, lease_seconds: int) -> ClaimedJob | None:
        now = datetime.now(UTC)
        job = await self._session.scalar(
            select(ProcessingJobModel)
            .where(
                or_(
                    (
                        (ProcessingJobModel.status == JobStatus.QUEUED.value)
                        & (ProcessingJobModel.available_at <= now)
                    ),
                    (
                        (ProcessingJobModel.status == JobStatus.PROCESSING.value)
                        & (ProcessingJobModel.lease_expires_at < now)
                    ),
                )
            )
            .order_by(ProcessingJobModel.available_at, ProcessingJobModel.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        document = await self._session.get(DocumentModel, job.document_id, with_for_update=True)
        if document is None:
            job.status = JobStatus.CANCELLED.value
            job.finished_at = now
            return None
        if document.status in {DocumentStatus.DELETING.value, DocumentStatus.DELETED.value}:
            job.status = JobStatus.CANCELLED.value
            job.finished_at = now
            job.lease_expires_at = None
            return None
        if job.attempt_count >= job.max_attempts:
            job.status = JobStatus.FAILED.value
            job.failure_code = "PROCESSING_RETRIES_EXHAUSTED"
            job.failure_message = "后台处理多次中断，请手动重试"
            job.finished_at = now
            job.lease_expires_at = None
            document.status = DocumentStatus.FAILED.value
            document.failure_code = job.failure_code
            document.failure_message = job.failure_message
            return None

        job.status = JobStatus.PROCESSING.value
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        document.status = DocumentStatus.PARSING.value
        document.failure_code = None
        document.failure_message = None
        document.updated_at = now
        await self._session.flush()
        return ClaimedJob(
            id=job.id,
            public_id=job.public_id,
            workspace_id=job.workspace_id,
            document_id=document.id,
            document_public_id=document.public_id,
            object_key=document.object_key,
            media_type=document.media_type,
            source_sha256=document.sha256,
        )

    async def complete_job(self, *, job: ClaimedJob, parsed: ParsedDocument) -> None:
        job_model = await self._session.get(ProcessingJobModel, job.id, with_for_update=True)
        document = await self._session.get(DocumentModel, job.document_id, with_for_update=True)
        if job_model is None or document is None:
            return
        if job_model.status == JobStatus.SUCCEEDED.value:
            return
        if document.status in {DocumentStatus.DELETING.value, DocumentStatus.DELETED.value}:
            job_model.status = JobStatus.CANCELLED.value
            job_model.finished_at = datetime.now(UTC)
            job_model.lease_expires_at = None
            return

        version = await self._session.scalar(
            select(DocumentVersionModel).where(
                DocumentVersionModel.document_id == document.id,
                DocumentVersionModel.source_sha256 == job.source_sha256,
                DocumentVersionModel.parser_name == parsed.parser_name,
                DocumentVersionModel.parser_version == parsed.parser_version,
            )
        )
        if version is None:
            latest_version = await self._session.scalar(
                select(func.coalesce(func.max(DocumentVersionModel.version_no), 0)).where(
                    DocumentVersionModel.document_id == document.id
                )
            )
            version = DocumentVersionModel(
                document_id=document.id,
                workspace_id=job.workspace_id,
                version_no=int(latest_version or 0) + 1,
                source_sha256=job.source_sha256,
                parser_name=parsed.parser_name,
                parser_version=parsed.parser_version,
                status="ready",
                page_count=len(parsed.contents),
            )
            self._session.add(version)
            await self._session.flush()
            self._session.add_all(
                DocumentPageModel(
                    document_version_id=version.id,
                    workspace_id=job.workspace_id,
                    page_number=content.ordinal,
                    content=content.content,
                    char_count=len(content.content),
                    locator_kind=content.locator.kind.value,
                    locator_position=content.locator.position,
                    locator_title=content.locator.title,
                    locator_path=list(content.locator.path),
                )
                for content in parsed.contents
            )

        document.active_version_id = version.id
        document.page_count = version.page_count
        document.status = DocumentStatus.READY.value
        document.failure_code = None
        document.failure_message = None
        document.updated_at = datetime.now(UTC)
        job_model.status = JobStatus.SUCCEEDED.value
        job_model.failure_code = None
        job_model.failure_message = None
        job_model.finished_at = datetime.now(UTC)
        job_model.lease_expires_at = None

    async def fail_job(
        self,
        *,
        job: ClaimedJob,
        failure_code: str,
        failure_message: str,
    ) -> None:
        job_model = await self._session.get(ProcessingJobModel, job.id, with_for_update=True)
        document = await self._session.get(DocumentModel, job.document_id, with_for_update=True)
        if job_model is None or document is None:
            return
        if job_model.status in {JobStatus.SUCCEEDED.value, JobStatus.CANCELLED.value}:
            return
        job_model.status = JobStatus.FAILED.value
        job_model.failure_code = failure_code
        job_model.failure_message = failure_message
        job_model.finished_at = datetime.now(UTC)
        job_model.lease_expires_at = None
        if document.status not in {DocumentStatus.DELETING.value, DocumentStatus.DELETED.value}:
            document.status = DocumentStatus.FAILED.value
            document.failure_code = failure_code
            document.failure_message = failure_message
            document.updated_at = datetime.now(UTC)


class SqlAlchemyDocumentsUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self.documents: DocumentRepository

    async def __aenter__(self) -> SqlAlchemyDocumentsUnitOfWork:
        self._session = self._session_factory()
        self.documents = SqlAlchemyDocumentRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        if self._session.in_transaction():
            await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Unit of work is not active")
        await self._session.commit()
