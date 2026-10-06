"""Bounded page-stratified coverage and merging for multi-batch study overviews."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

OVERVIEW_VERSION = "page-stratified-overview-v1"
OVERVIEW_BATCH_SIZE = 8
MAX_OVERVIEW_BATCHES = 8
MAX_OVERVIEW_SOURCES = OVERVIEW_BATCH_SIZE * MAX_OVERVIEW_BATCHES


@dataclass(frozen=True)
class OverviewChunk:
    id: UUID
    document_id: UUID
    version_id: int
    unit: int
    ordinal: int


def spread_indices(count: int, take: int) -> list[int]:
    """Include both ends rather than losing opening and concluding sections."""
    if take <= 0:
        return []
    if take == 1:
        return [0]
    return [index * (count - 1) // (take - 1) for index in range(min(take, count))]


def select_overview_chunks(chunks: list[OverviewChunk]) -> list[OverviewChunk]:
    documents: dict[UUID, dict[int, list[OverviewChunk]]] = {}
    for chunk in sorted(chunks, key=lambda c: (str(c.document_id), c.unit, c.ordinal)):
        documents.setdefault(chunk.document_id, {}).setdefault(chunk.unit, []).append(chunk)
    if not documents:
        return []
    # First allocate by page count so dense pages cannot crowd out other sections.
    quotas = {doc: 1 for doc in documents}
    pages = {doc: len(units) for doc, units in documents.items()}
    while sum(quotas.values()) < min(MAX_OVERVIEW_SOURCES, sum(pages.values())):
        candidates = [doc for doc in documents if quotas[doc] < pages[doc]]
        doc = max(candidates, key=lambda key: pages[key] / (quotas[key] + 1))
        quotas[doc] += 1
    selected: list[OverviewChunk] = []
    for doc, units in documents.items():
        page_numbers = list(units)
        for position in spread_indices(len(page_numbers), quotas[doc]):
            selected.append(units[page_numbers[position]][0])
    # If all pages fit, use remaining slots for dense pages' interior/end chunks.
    selected_ids = {chunk.id for chunk in selected}
    remaining: dict[tuple[UUID, int], list[OverviewChunk]] = defaultdict(list)
    for chunk in chunks:
        if chunk.id not in selected_ids:
            remaining[(chunk.document_id, chunk.unit)].append(chunk)
    while remaining and len(selected) < MAX_OVERVIEW_SOURCES:
        for key in sorted(remaining, key=lambda k: (str(k[0]), k[1])):
            values = sorted(remaining[key], key=lambda c: c.ordinal)
            # Alternate from the end then interior, preserving page breadth first.
            selected.append(values.pop())
            if values:
                remaining[key] = values
            else:
                del remaining[key]
            if len(selected) == MAX_OVERVIEW_SOURCES:
                break
    return sorted(selected, key=lambda c: (str(c.document_id), c.unit, c.ordinal))


def overview_batch_index(stage: str) -> int:
    if stage == "summary":
        return 0
    allowed = [f"overview_{index}" for index in range(1, MAX_OVERVIEW_BATCHES)]
    if stage not in allowed:
        raise ValueError("Unknown overview stage")
    return int(stage.removeprefix("overview_"))


def merge_overview_answers(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> dict[str, Any]:
    """Combine already validated batches without a lossy second model summary."""
    claims: list[dict[str, Any]] = []
    seen: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    explanations: list[str] = []
    for answer in (previous or {}, current):
        for claim in answer.get("claims", []):
            key = (
                " ".join(claim["text"].split()).casefold(),
                tuple(sorted({str(c.get("document_id", "")) for c in claim["citations"]})),
            )
            if key not in seen:
                copied = {**claim, "citations": list(claim["citations"])}
                seen[key] = copied
                claims.append(copied)
            else:
                existing = seen[key]["citations"]
                known = {(c["source_id"], c.get("quote")) for c in existing}
                for citation in claim["citations"]:
                    if (
                        len(existing) < 4
                        and (citation["source_id"], citation.get("quote")) not in known
                    ):
                        existing.append(citation)
                        known.add((citation["source_id"], citation.get("quote")))
        for paragraph in answer.get("explanation", "").split("\n\n"):
            explanation = paragraph.strip()
            if explanation and explanation not in explanations:
                explanations.append(explanation)
    claims.sort(
        key=lambda claim: min(
            (str(c.get("document_id", "")), c["unit"]) for c in claim["citations"]
        )
    )
    return {
        "insufficient_evidence": not bool(claims),
        "claims": claims,
        **({"explanation": "\n\n".join(explanations)} if explanations else {}),
    }


def overview_batches(selected: list[OverviewChunk]) -> list[list[OverviewChunk]]:
    """Each batch spans the source range; the first also includes every document."""
    remaining = list(selected)
    batches: list[list[OverviewChunk]] = []
    while remaining:
        batch: list[OverviewChunk] = []
        if not batches:
            documents: set[UUID] = set()
            for chunk in remaining:
                if chunk.document_id not in documents:
                    documents.add(chunk.document_id)
                    batch.append(chunk)
            ids = {chunk.id for chunk in batch}
            remaining = [chunk for chunk in remaining if chunk.id not in ids]
        take = min(OVERVIEW_BATCH_SIZE - len(batch), len(remaining))
        batch.extend(remaining[index] for index in spread_indices(len(remaining), take))
        ids = {chunk.id for chunk in batch}
        remaining = [chunk for chunk in remaining if chunk.id not in ids]
        batches.append(sorted(batch, key=lambda c: (str(c.document_id), c.unit, c.ordinal)))
    return batches
