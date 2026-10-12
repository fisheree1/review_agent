"""One hybrid (vector + full-text) candidate search shared by every retrieval path.

Callers build a scoped ``select`` whose first two columns are ``DocumentChunk`` and
``DocumentPageModel`` and whose last column is the cosine distance; every tenant, version and
deletion filter stays in that query. This module only adds ordering, the lexical match and
reciprocal-rank fusion, so the security-relevant predicates are never duplicated here.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Row, Select, func, literal_column
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.infrastructure.models import DocumentPageModel
from app.rag.domain import Evidence
from app.rag.lexical import lexical_query
from app.rag.models import DocumentChunk

# Must match the expression of ix_document_chunks_search exactly so the GIN index is used.
SEARCH_CONFIG: ColumnElement[Any] = literal_column("'simple'::regconfig")
SEARCH_VECTOR = func.to_tsvector(SEARCH_CONFIG, DocumentChunk.search_text)

CANDIDATES_PER_RANKING = 12
RRF_K = 60
# Model still decides sufficiency; this only keeps clearly unrelated *vector* neighbours out.
# Lexical hits are kept regardless of vector distance: exact-term matches the embedding
# under-rates are what hybrid retrieval is for.
SEMANTIC_MAX_DISTANCE = 0.8


def reciprocal_rank_fusion[K: Hashable](
    rankings: Iterable[Sequence[K]], *, k: int = RRF_K
) -> dict[K, float]:
    """Sum 1/(k + rank) over rankings; insertion order breaks ties deterministically."""
    scores: dict[K, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1 / (k + rank)
    return scores


def lexical_match(query_text: str) -> tuple[ColumnElement[bool], ColumnElement[Any]] | None:
    query = lexical_query(query_text)
    if query is None:
        return None
    tsquery = func.to_tsquery(SEARCH_CONFIG, query)
    return SEARCH_VECTOR.bool_op("@@")(tsquery), func.ts_rank_cd(SEARCH_VECTOR, tsquery)


def page_locator(page: DocumentPageModel) -> dict[str, Any]:
    return {
        "kind": page.locator_kind,
        "position": page.locator_position,
        "title": page.locator_title,
        "path": page.locator_path,
    }


def evidence_from_row(
    row: Row[Any], *, document_id: UUID | None = None, version_id: int | None = None
) -> Evidence:
    chunk: DocumentChunk = row[0]
    page: DocumentPageModel = row[1]
    return Evidence(
        chunk.public_id,
        chunk.content,
        chunk.unit,
        page_locator(page),
        1 - float(row[-1]),
        document_id,
        version_id,
    )


async def hybrid_candidates(
    session: AsyncSession,
    query: Select[Any],
    *,
    distance: ColumnElement[Any],
    query_text: str,
    candidates: int = CANDIDATES_PER_RANKING,
) -> list[tuple[Row[Any], float]]:
    """Return fused candidate rows (best first) with their RRF scores."""
    semantic = (
        await session.execute(
            query.order_by(distance, DocumentChunk.ordinal, DocumentChunk.id).limit(candidates)
        )
    ).all()
    lexical: Sequence[Row[Any]] = ()
    match = lexical_match(query_text)
    if match is not None:
        condition, rank = match
        lexical = (
            await session.execute(
                query.where(condition)
                .order_by(rank.desc(), DocumentChunk.ordinal, DocumentChunk.id)
                .limit(candidates)
            )
        ).all()

    rows: dict[UUID, Row[Any]] = {}
    semantic_ids: list[UUID] = []
    for row in semantic:
        if float(row[-1]) > SEMANTIC_MAX_DISTANCE:
            continue
        semantic_ids.append(row[0].public_id)
        rows.setdefault(row[0].public_id, row)
    lexical_ids: list[UUID] = []
    for row in lexical:
        lexical_ids.append(row[0].public_id)
        rows.setdefault(row[0].public_id, row)

    scores = reciprocal_rank_fusion((semantic_ids, lexical_ids))
    ordered = sorted(scores, key=lambda key: scores[key], reverse=True)
    return [(rows[key], scores[key]) for key in ordered]
