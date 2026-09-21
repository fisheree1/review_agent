from __future__ import annotations

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile, status

from app.core.auth import Principal, get_current_principal
from app.core.config import Settings, get_settings
from app.core.database import async_session_factory
from app.documents.application.ports import DocumentStorage
from app.documents.application.service import DocumentService
from app.documents.infrastructure.repository import SqlAlchemyDocumentsUnitOfWork
from app.documents.infrastructure.storage import MinioDocumentStorage
from app.documents.schemas import (
    ContentLocationResponse,
    ContentResponse,
    DocumentContentResponse,
    DocumentListResponse,
    DocumentPagesResponse,
    DocumentResponse,
    PageResponse,
    UploadResponse,
)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@lru_cache
def get_document_storage() -> DocumentStorage:
    settings = get_settings()
    return MinioDocumentStorage(
        endpoint=settings.storage_endpoint,
        access_key=settings.storage_access_key,
        secret_key=settings.storage_secret_key.get_secret_value(),
        bucket=settings.storage_bucket,
        secure=settings.storage_secure,
    )


def get_document_service(
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[DocumentStorage, Depends(get_document_storage)],
) -> DocumentService:
    return DocumentService(
        unit_of_work_factory=lambda: SqlAlchemyDocumentsUnitOfWork(async_session_factory),
        storage=storage,
        max_upload_bytes=settings.max_upload_bytes,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> DocumentListResponse:
    result = await service.list(
        workspace_public_id=principal.workspace_public_id,
        limit=limit,
        cursor=cursor,
    )
    return DocumentListResponse(
        items=[DocumentResponse.from_entity(document) for document in result.items],
        next_cursor=result.next_cursor,
    )


@router.post("", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    file: Annotated[UploadFile, File(description="PDF, DOCX, or PPTX learning material")],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> UploadResponse:
    result = await service.upload(
        workspace_public_id=principal.workspace_public_id,
        source=file,
        idempotency_key=idempotency_key,
    )
    return UploadResponse(
        document=DocumentResponse.from_entity(result.document),
        deduplicated=result.deduplicated,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    document = await service.get(
        workspace_public_id=principal.workspace_public_id,
        document_public_id=document_id,
    )
    return DocumentResponse.from_entity(document)


@router.get("/{document_id}/pages", response_model=DocumentPagesResponse)
async def get_document_pages(
    document_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    page: Annotated[int, Query(ge=1)] = 1,
) -> DocumentPagesResponse:
    result = await service.content(
        workspace_public_id=principal.workspace_public_id,
        document_public_id=document_id,
        ordinal=page,
    )
    return DocumentPagesResponse(
        document_id=document_id,
        page_count=result.content_count,
        pages=[
            PageResponse(page_number=content.ordinal, content=content.content)
            for content in result.contents
        ],
    )


@router.get("/{document_id}/content", response_model=DocumentContentResponse)
async def get_document_content(
    document_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    ordinal: Annotated[int, Query(ge=1)] = 1,
) -> DocumentContentResponse:
    result = await service.content(
        workspace_public_id=principal.workspace_public_id,
        document_public_id=document_id,
        ordinal=ordinal,
    )
    return DocumentContentResponse(
        document_id=document_id,
        content_count=result.content_count,
        contents=[ContentResponse.from_entity(content) for content in result.contents],
        locations=[ContentLocationResponse.from_entity(location) for location in result.locations],
    )


@router.post("/{document_id}:retry", response_model=DocumentResponse, status_code=202)
async def retry_document(
    document_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DocumentResponse:
    document = await service.retry(
        workspace_public_id=principal.workspace_public_id,
        document_public_id=document_id,
        idempotency_key=idempotency_key,
    )
    return DocumentResponse.from_entity(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> Response:
    await service.delete(
        workspace_public_id=principal.workspace_public_id,
        document_public_id=document_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
