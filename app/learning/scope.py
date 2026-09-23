from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApplicationError
from app.documents.infrastructure.models import DocumentModel, DocumentPageModel, WorkspaceModel
from app.learning.models import Collection, CollectionDocument
from app.rag.domain import Evidence
from app.rag.models import DocumentChunk, DocumentIndex

ScopeSnapshot = list[dict[str, Any]]


def missing() -> ApplicationError:
    return ApplicationError(
        code="RESOURCE_NOT_FOUND", message="资料或学习记录不存在", status_code=404
    )


async def workspace_id(session: AsyncSession, public_id: UUID) -> int:
    found = await session.scalar(
        select(WorkspaceModel.id).where(
            WorkspaceModel.public_id == public_id, WorkspaceModel.status == "active"
        )
    )
    if found is None:
        raise missing()
    return found


async def resolve_scope(
    session: AsyncSession,
    workspace: int,
    documents: list[UUID],
    collections: list[UUID],
    profile: str,
) -> ScopeSnapshot:
    if len(documents) > 5 or len(collections) > 5:
        raise ApplicationError(
            code="SCOPE_INVALID", message="最多选择 5 份资料或 5 个集合", status_code=422
        )
    selected = set(documents)
    if collections:
        rows = (
            await session.execute(
                select(Collection.public_id, DocumentModel.public_id)
                .join(
                    CollectionDocument,
                    and_(
                        CollectionDocument.collection_id == Collection.id,
                        CollectionDocument.workspace_id == Collection.workspace_id,
                    ),
                )
                .join(
                    DocumentModel,
                    and_(
                        DocumentModel.id == CollectionDocument.document_id,
                        DocumentModel.workspace_id == Collection.workspace_id,
                    ),
                )
                .where(
                    Collection.workspace_id == workspace,
                    Collection.public_id.in_(collections),
                    DocumentModel.deleted_at.is_(None),
                )
            )
        ).all()
        found_collections = set(
            await session.scalars(
                select(Collection.public_id).where(
                    Collection.workspace_id == workspace, Collection.public_id.in_(collections)
                )
            )
        )
        if found_collections != set(collections):
            raise missing()
        selected.update(row[1] for row in rows)
    if not selected or len(selected) > 5:
        raise ApplicationError(code="SCOPE_INVALID", message="请选择 1–5 份资料", status_code=422)
    document_rows = (
        await session.execute(
            select(DocumentModel, DocumentIndex.id)
            .outerjoin(
                DocumentIndex,
                and_(
                    DocumentIndex.workspace_id == DocumentModel.workspace_id,
                    DocumentIndex.document_version_id == DocumentModel.active_version_id,
                    DocumentIndex.profile == profile,
                    DocumentIndex.status == "ready",
                ),
            )
            .where(
                DocumentModel.workspace_id == workspace,
                DocumentModel.public_id.in_(selected),
                DocumentModel.deleted_at.is_(None),
            )
        )
    ).all()
    if {row[0].public_id for row in document_rows} != selected:
        raise missing()
    if any(
        row[0].status != "ready" or row[0].active_version_id is None or row[1] is None
        for row in document_rows
    ):
        raise ApplicationError(
            code="INDEX_NOT_READY", message="所选资料尚未完成问答索引", status_code=409
        )
    return [
        {
            "document_id": str(document.public_id),
            "version_id": document.active_version_id,
            "filename": document.original_filename,
        }
        for document, _ in sorted(document_rows, key=lambda row: str(row[0].public_id))
    ]


async def snapshot_ready(
    session: AsyncSession, workspace: int, snapshot: ScopeSnapshot, profile: str
) -> bool:
    versions = {item["version_id"] for item in snapshot}
    rows = (
        await session.execute(
            select(DocumentModel.public_id, DocumentModel.active_version_id)
            .join(
                DocumentIndex,
                and_(
                    DocumentIndex.document_version_id == DocumentModel.active_version_id,
                    DocumentIndex.workspace_id == DocumentModel.workspace_id,
                ),
            )
            .where(
                DocumentModel.workspace_id == workspace,
                DocumentModel.status == "ready",
                DocumentModel.deleted_at.is_(None),
                DocumentModel.active_version_id.in_(versions),
                DocumentIndex.status == "ready",
                DocumentIndex.profile == profile,
            )
        )
    ).all()
    return {(str(doc), version) for doc, version in rows} == {
        (item["document_id"], item["version_id"]) for item in snapshot
    }


async def retrieve_sources(
    session: AsyncSession,
    workspace: int,
    snapshot: ScopeSnapshot,
    profile: str,
    vector: list[float],
    query_text: str,
    *,
    limit: int = 8,
) -> list[Evidence]:
    """All workspace, selected version, active version and deletion filters are SQL predicates."""
    versions = [item["version_id"] for item in snapshot]
    distance = DocumentChunk.embedding.cosine_distance(vector)
    query = (
        select(
            DocumentChunk,
            DocumentPageModel,
            DocumentModel.public_id,
            DocumentIndex.document_version_id,
            distance.label("distance"),
        )
        .join(
            DocumentIndex,
            and_(
                DocumentIndex.id == DocumentChunk.index_id,
                DocumentIndex.workspace_id == DocumentChunk.workspace_id,
            ),
        )
        .join(
            DocumentModel,
            and_(
                DocumentModel.active_version_id == DocumentIndex.document_version_id,
                DocumentModel.workspace_id == DocumentIndex.workspace_id,
            ),
        )
        .join(
            DocumentPageModel,
            and_(
                DocumentPageModel.document_version_id == DocumentIndex.document_version_id,
                DocumentPageModel.workspace_id == DocumentChunk.workspace_id,
                DocumentPageModel.page_number == DocumentChunk.unit,
            ),
        )
        .where(
            DocumentChunk.workspace_id == workspace,
            DocumentIndex.workspace_id == workspace,
            DocumentIndex.document_version_id.in_(versions),
            DocumentIndex.profile == profile,
            DocumentIndex.status == "ready",
            DocumentModel.status == "ready",
            DocumentModel.deleted_at.is_(None),
            DocumentChunk.embedding.is_not(None),
        )
    )
    lexical_rank = func.ts_rank_cd(
        func.to_tsvector("simple", DocumentChunk.content),
        func.plainto_tsquery("simple", query_text),
    )
    scores: dict[UUID, float] = defaultdict(float)
    evidence: dict[UUID, Evidence] = {}
    for version in versions:
        scoped = query.where(DocumentIndex.document_version_id == version)
        semantic = (
            await session.execute(scoped.order_by(distance, DocumentChunk.id).limit(12))
        ).all()
        lexical = (
            await session.execute(
                scoped.where(lexical_rank > 0)
                .order_by(lexical_rank.desc(), DocumentChunk.id)
                .limit(12)
            )
        ).all()
        for ranking in (semantic, lexical):
            for rank, (chunk, page, document_id, version_id, cosine) in enumerate(ranking, start=1):
                if float(cosine) > 0.8:
                    continue
                scores[chunk.public_id] += 1 / (60 + rank)
                evidence[chunk.public_id] = Evidence(
                    chunk.public_id,
                    chunk.content,
                    chunk.unit,
                    {
                        "kind": page.locator_kind,
                        "position": page.locator_position,
                        "title": page.locator_title,
                        "path": page.locator_path,
                    },
                    1 - float(cosine),
                    document_id,
                    version_id,
                )
    ordered = sorted(scores, key=lambda source_id: scores[source_id], reverse=True)
    # Reserve one relevant source from each selected document before filling by rank.
    result: list[Evidence] = []
    for item in snapshot:
        match = next(
            (
                evidence[source_id]
                for source_id in ordered
                if str(evidence[source_id].document_id) == item["document_id"]
            ),
            None,
        )
        if match is not None:
            result.append(match)
    for source_id in ordered:
        if len(result) >= limit:
            break
        source = evidence[source_id]
        if source not in result:
            result.append(source)
    return result


async def quiz_sources(
    session: AsyncSession,
    workspace: int,
    snapshot: ScopeSnapshot,
    profile: str,
    *,
    limit: int = 12,
) -> list[Evidence]:
    """Choose bounded, distributed evidence for a quiz blueprint without model-side tool access."""
    versions = [item["version_id"] for item in snapshot]
    query = (
        select(
            DocumentChunk,
            DocumentPageModel,
            DocumentModel.public_id,
            DocumentIndex.document_version_id,
        )
        .join(
            DocumentIndex,
            and_(
                DocumentIndex.id == DocumentChunk.index_id,
                DocumentIndex.workspace_id == DocumentChunk.workspace_id,
            ),
        )
        .join(
            DocumentModel,
            and_(
                DocumentModel.active_version_id == DocumentIndex.document_version_id,
                DocumentModel.workspace_id == DocumentIndex.workspace_id,
            ),
        )
        .join(
            DocumentPageModel,
            and_(
                DocumentPageModel.document_version_id == DocumentIndex.document_version_id,
                DocumentPageModel.workspace_id == DocumentChunk.workspace_id,
                DocumentPageModel.page_number == DocumentChunk.unit,
            ),
        )
        .where(
            DocumentChunk.workspace_id == workspace,
            DocumentIndex.document_version_id.in_(versions),
            DocumentIndex.profile == profile,
            DocumentIndex.status == "ready",
            DocumentModel.status == "ready",
            DocumentModel.deleted_at.is_(None),
            DocumentChunk.embedding.is_not(None),
        )
    )
    per_document: dict[UUID, list[Evidence]] = defaultdict(list)
    for version in versions:
        rows = (
            await session.execute(
                query.where(DocumentIndex.document_version_id == version)
                .order_by(DocumentChunk.ordinal)
                .limit(limit)
            )
        ).all()
        for chunk, page, document_id, version_id in rows:
            per_document[document_id].append(
                Evidence(
                    chunk.public_id,
                    chunk.content,
                    chunk.unit,
                    {
                        "kind": page.locator_kind,
                        "position": page.locator_position,
                        "title": page.locator_title,
                        "path": page.locator_path,
                    },
                    0,
                    document_id,
                    version_id,
                )
            )
    result: list[Evidence] = []
    while len(result) < limit and any(per_document.values()):
        for document_id in sorted(per_document, key=str):
            if per_document[document_id] and len(result) < limit:
                result.append(per_document[document_id].pop(0))
    return result
