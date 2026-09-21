from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.documents.domain.entities import (
    CitationLocator,
    CitationLocatorKind,
    Document,
    DocumentContent,
    DocumentContentLocation,
    DocumentStatus,
)


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    media_type: str
    byte_size: int
    status: DocumentStatus
    page_count: int | None
    content_count: int | None
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
            content_count=document.page_count,
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


class CitationLocatorResponse(BaseModel):
    kind: CitationLocatorKind
    position: int = Field(gt=0)
    title: str | None
    path: list[str]

    @classmethod
    def from_entity(cls, locator: CitationLocator) -> CitationLocatorResponse:
        return cls(
            kind=locator.kind,
            position=locator.position,
            title=locator.title,
            path=list(locator.path),
        )


class ContentResponse(BaseModel):
    ordinal: int = Field(gt=0)
    content: str
    citation_locator: CitationLocatorResponse

    @classmethod
    def from_entity(cls, content: DocumentContent) -> ContentResponse:
        return cls(
            ordinal=content.ordinal,
            content=content.content,
            citation_locator=CitationLocatorResponse.from_entity(content.locator),
        )


class ContentLocationResponse(BaseModel):
    ordinal: int = Field(gt=0)
    citation_locator: CitationLocatorResponse

    @classmethod
    def from_entity(cls, location: DocumentContentLocation) -> ContentLocationResponse:
        return cls(
            ordinal=location.ordinal,
            citation_locator=CitationLocatorResponse.from_entity(location.locator),
        )


class DocumentContentResponse(BaseModel):
    document_id: UUID
    content_count: int = Field(ge=0)
    contents: list[ContentResponse]
    locations: list[ContentLocationResponse]


class PageResponse(BaseModel):
    page_number: int = Field(gt=0)
    content: str


class DocumentPagesResponse(BaseModel):
    document_id: UUID
    page_count: int = Field(ge=0)
    pages: list[PageResponse]
