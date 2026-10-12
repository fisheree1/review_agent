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


class CitationLocatorKind(StrEnum):
    PAGE = "page"
    HEADING = "heading"
    SLIDE = "slide"


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
class DocumentListFilters:
    search: str = ""
    statuses: tuple[DocumentStatus, ...] = ()
    oldest_first: bool = False
    collection_public_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DocumentListPage:
    items: tuple[Document, ...]
    next_position: DocumentListPosition | None


@dataclass(frozen=True, slots=True)
class CitationLocator:
    kind: CitationLocatorKind
    position: int
    title: str | None = None
    path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentContent:
    ordinal: int
    content: str
    locator: CitationLocator


@dataclass(frozen=True, slots=True)
class DocumentContentLocation:
    ordinal: int
    locator: CitationLocator


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    contents: tuple[DocumentContent, ...]
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
    media_type: str
    source_sha256: str
    # Fencing token: a claim only owns the job while the row still has this attempt number.
    attempt: int = 0


@dataclass(frozen=True, slots=True)
class DeleteTarget:
    document: Document
    object_key: str


@dataclass(frozen=True, slots=True)
class DocumentSource:
    object_key: str
    media_type: str
    status: DocumentStatus
