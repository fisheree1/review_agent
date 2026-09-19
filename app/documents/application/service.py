from __future__ import annotations

import asyncio
import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from app.core.errors import ApplicationError
from app.documents.application.errors import StorageOperationError
from app.documents.application.ports import (
    DocumentStorage,
    DuplicateDocumentError,
    UnitOfWorkFactory,
    UploadSource,
)
from app.documents.application.upload_validation import StagedUpload, stage_pdf_upload
from app.documents.domain.entities import (
    Document,
    DocumentListPosition,
    DocumentPage,
    DocumentStatus,
)

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: Document
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class DocumentListResult:
    items: tuple[Document, ...]
    next_cursor: str | None


class DocumentService:
    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        storage: DocumentStorage,
        max_upload_bytes: int,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._storage = storage
        self._max_upload_bytes = max_upload_bytes

    @staticmethod
    def _request_key(value: str | None) -> str:
        if value is None:
            return str(uuid4())
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
            raise ApplicationError(
                code="INVALID_IDEMPOTENCY_KEY",
                message="Idempotency-Key 只能包含字母、数字、点、下划线、冒号和短横线",
                status_code=422,
            )
        return value

    async def upload(
        self,
        *,
        workspace_public_id: UUID,
        source: UploadSource,
        idempotency_key: str | None,
    ) -> UploadResult:
        staged = await stage_pdf_upload(source, max_bytes=self._max_upload_bytes)
        try:
            return await self._persist_upload(
                workspace_public_id=workspace_public_id,
                staged=staged,
                upload_key=self._request_key(idempotency_key),
            )
        finally:
            await asyncio.to_thread(staged.path.unlink, missing_ok=True)

    async def _persist_upload(
        self,
        *,
        workspace_public_id: UUID,
        staged: StagedUpload,
        upload_key: str,
    ) -> UploadResult:
        existing = await self._find_existing_upload(
            workspace_public_id=workspace_public_id,
            upload_key=upload_key,
            sha256=staged.sha256,
        )
        if existing is not None and not (
            existing.status == DocumentStatus.FAILED
            and existing.failure_code == "STORAGE_UPLOAD_FAILED"
        ):
            return UploadResult(document=existing, deduplicated=True)

        document = existing
        if document is None:
            document_public_id = uuid4()
            object_key = f"{workspace_public_id}/{document_public_id}/source.pdf"
            try:
                async with self._unit_of_work_factory() as unit_of_work:
                    workspace_id = await unit_of_work.documents.ensure_workspace(
                        workspace_public_id
                    )
                    document = await unit_of_work.documents.create_document(
                        workspace_id=workspace_id,
                        workspace_public_id=workspace_public_id,
                        public_id=document_public_id,
                        original_filename=staged.original_filename,
                        media_type=staged.media_type,
                        byte_size=staged.byte_size,
                        sha256=staged.sha256,
                        object_key=object_key,
                        upload_key=upload_key,
                    )
                    await unit_of_work.commit()
            except DuplicateDocumentError:
                document = await self._find_existing_upload(
                    workspace_public_id=workspace_public_id,
                    upload_key=upload_key,
                    sha256=staged.sha256,
                )
                if document is None:
                    raise ApplicationError(
                        code="DUPLICATE_UPLOAD_CONFLICT",
                        message="相同请求正在处理，请稍后查询资料状态",
                        status_code=409,
                    ) from None

        object_key = f"{workspace_public_id}/{document.public_id}/source.pdf"
        try:
            await self._storage.upload(
                object_key=object_key,
                source_path=staged.path,
                length=staged.byte_size,
            )
        except StorageOperationError as exc:
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.documents.mark_upload_failed(
                    workspace_public_id=workspace_public_id,
                    document_public_id=document.public_id,
                    failure_code="STORAGE_UPLOAD_FAILED",
                    failure_message="文件保存失败，请重新上传",
                )
                await unit_of_work.commit()
            raise ApplicationError(
                code="STORAGE_UNAVAILABLE",
                message="文件暂时无法保存，请稍后重试",
                status_code=503,
                details={"document_id": str(document.public_id)},
            ) from exc

        async with self._unit_of_work_factory() as unit_of_work:
            queued = await unit_of_work.documents.queue_initial_processing(
                workspace_public_id=workspace_public_id,
                document_public_id=document.public_id,
                job_public_id=uuid4(),
            )
            await unit_of_work.commit()
        return UploadResult(document=queued, deduplicated=existing is not None)

    async def _find_existing_upload(
        self,
        *,
        workspace_public_id: UUID,
        upload_key: str,
        sha256: str,
    ) -> Document | None:
        async with self._unit_of_work_factory() as unit_of_work:
            by_key = await unit_of_work.documents.find_by_upload_key(
                workspace_public_id=workspace_public_id,
                upload_key=upload_key,
            )
            if by_key is not None and by_key.sha256 != sha256:
                raise ApplicationError(
                    code="IDEMPOTENCY_KEY_REUSED",
                    message="同一个 Idempotency-Key 不能用于不同文件",
                    status_code=409,
                )
            if by_key is not None:
                return by_key
            return await unit_of_work.documents.find_by_hash(
                workspace_public_id=workspace_public_id,
                sha256=sha256,
            )

    async def get(self, *, workspace_public_id: UUID, document_public_id: UUID) -> Document:
        async with self._unit_of_work_factory() as unit_of_work:
            document = await unit_of_work.documents.get_document(
                workspace_public_id=workspace_public_id,
                document_public_id=document_public_id,
            )
        if document is None:
            raise ApplicationError(
                code="DOCUMENT_NOT_FOUND",
                message="没有找到该资料",
                status_code=404,
            )
        return document

    @staticmethod
    def _decode_cursor(cursor: str | None) -> DocumentListPosition | None:
        if cursor is None:
            return None
        try:
            padding = "=" * (-len(cursor) % 4)
            decoded = base64.urlsafe_b64decode(f"{cursor}{padding}").decode("utf-8")
            created_at_value, internal_id_value = decoded.rsplit("|", 1)
            created_at = datetime.fromisoformat(created_at_value)
            internal_id = int(internal_id_value)
            if created_at.tzinfo is None or internal_id <= 0:
                raise ValueError
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise ApplicationError(
                code="INVALID_CURSOR",
                message="资料列表游标无效，请重新加载列表",
                status_code=422,
            ) from exc
        return DocumentListPosition(created_at=created_at, internal_id=internal_id)

    @staticmethod
    def _encode_cursor(position: DocumentListPosition | None) -> str | None:
        if position is None:
            return None
        value = f"{position.created_at.isoformat()}|{position.internal_id}".encode()
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    async def list(
        self,
        *,
        workspace_public_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> DocumentListResult:
        async with self._unit_of_work_factory() as unit_of_work:
            page = await unit_of_work.documents.list_documents(
                workspace_public_id=workspace_public_id,
                limit=limit,
                after=self._decode_cursor(cursor),
            )
        return DocumentListResult(
            items=page.items,
            next_cursor=self._encode_cursor(page.next_position),
        )

    async def pages(
        self, *, workspace_public_id: UUID, document_public_id: UUID
    ) -> tuple[DocumentPage, ...]:
        document = await self.get(
            workspace_public_id=workspace_public_id,
            document_public_id=document_public_id,
        )
        if document.status != DocumentStatus.READY:
            raise ApplicationError(
                code="DOCUMENT_NOT_READY",
                message="资料尚未完成解析",
                status_code=409,
                details={"status": document.status.value},
            )
        async with self._unit_of_work_factory() as unit_of_work:
            return await unit_of_work.documents.list_pages(
                workspace_public_id=workspace_public_id,
                document_public_id=document_public_id,
            )

    async def retry(
        self,
        *,
        workspace_public_id: UUID,
        document_public_id: UUID,
        idempotency_key: str | None,
    ) -> Document:
        document = await self.get(
            workspace_public_id=workspace_public_id,
            document_public_id=document_public_id,
        )
        if document.failure_code == "STORAGE_UPLOAD_FAILED":
            raise ApplicationError(
                code="FILE_MUST_BE_REUPLOADED",
                message="原文件未保存成功，请重新上传文件",
                status_code=409,
            )

        retry_key = self._request_key(idempotency_key)
        async with self._unit_of_work_factory() as unit_of_work:
            try:
                queued = await unit_of_work.documents.queue_retry(
                    workspace_public_id=workspace_public_id,
                    document_public_id=document_public_id,
                    retry_key=retry_key,
                    job_public_id=uuid4(),
                )
            except LookupError:
                raise ApplicationError(
                    code="DOCUMENT_NOT_FOUND",
                    message="没有找到该资料",
                    status_code=404,
                ) from None
            except RuntimeError as exc:
                raise ApplicationError(
                    code="DOCUMENT_NOT_FAILED",
                    message="只有处理失败的资料可以重试，或使用原重试请求键查询结果",
                    status_code=409,
                    details={"status": document.status.value},
                ) from exc
            await unit_of_work.commit()
        return queued

    async def delete(self, *, workspace_public_id: UUID, document_public_id: UUID) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            target = await unit_of_work.documents.prepare_delete(
                workspace_public_id=workspace_public_id,
                document_public_id=document_public_id,
            )
            await unit_of_work.commit()
        if target is None or target.document.status == DocumentStatus.DELETED:
            return

        try:
            await self._storage.delete(object_key=target.object_key)
        except StorageOperationError as exc:
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.documents.mark_delete_failed(
                    workspace_public_id=workspace_public_id,
                    document_public_id=document_public_id,
                    failure_code="STORAGE_DELETE_FAILED",
                    failure_message="原文件删除失败，请重试删除",
                )
                await unit_of_work.commit()
            raise ApplicationError(
                code="DOCUMENT_DELETE_FAILED",
                message="资料暂时无法完全删除，请稍后重试",
                status_code=503,
            ) from exc

        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.documents.complete_delete(
                workspace_public_id=workspace_public_id,
                document_public_id=document_public_id,
            )
            await unit_of_work.commit()
