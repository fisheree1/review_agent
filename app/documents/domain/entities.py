from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PARSING = "parsing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Document:
    public_id: UUID
    workspace_public_id: UUID
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str
    status: DocumentStatus
    page_count: int | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentListPosition:
    created_at: datetime
    internal_id: int


@dataclass(frozen=True, slots=True)
class DocumentListPage:
    items: tuple[Document, ...]
    next_position: DocumentListPosition | None


@dataclass(frozen=True, slots=True)
class DocumentPage:
    page_number: int
    content: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    pages: tuple[DocumentPage, ...]
    parser_name: str
    parser_version: str


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: int
    public_id: UUID
    workspace_id: int
    document_id: int
    document_public_id: UUID
    object_key: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class DeleteTarget:
    document: Document
    object_key: str
