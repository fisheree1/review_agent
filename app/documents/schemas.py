from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.documents.domain.entities import Document, DocumentPage, DocumentStatus


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    media_type: str
    byte_size: int
    status: DocumentStatus
    page_count: int | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, document: Document) -> DocumentResponse:
        return cls(
            id=document.public_id,
            filename=document.original_filename,
            media_type=document.media_type,
            byte_size=document.byte_size,
            status=document.status,
            page_count=document.page_count,
            failure_code=document.failure_code,
            failure_message=document.failure_message,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class UploadResponse(BaseModel):
    document: DocumentResponse
    deduplicated: bool


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    next_cursor: str | None


class PageResponse(BaseModel):
    page_number: int = Field(gt=0)
    content: str

    @classmethod
    def from_entity(cls, page: DocumentPage) -> PageResponse:
        return cls(page_number=page.page_number, content=page.content)


class DocumentPagesResponse(BaseModel):
    document_id: UUID
    page_count: int = Field(ge=0)
    pages: list[PageResponse]
