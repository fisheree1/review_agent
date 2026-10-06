from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from uuid import UUID

DIMENSIONS = 1024
CHUNK_VERSION = "source-window-1500-180-v1"
PROMPT_VERSION = "cited-claims-v1"
STUDY_PROMPT_VERSION = "pdf-knowledge-sections-v2"
RETRIEVAL_VERSION = "exact-cosine-fts-rrf-v1"


class RagFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Scope:
    workspace: UUID
    document: UUID


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    unit: int
    start: int
    end: int
    content: str


@dataclass(frozen=True)
class Evidence:
    id: UUID
    content: str
    unit: int
    locator: dict[str, Any]
    similarity: float = 0.0
    document_id: UUID | None = None
    version_id: int | None = None


@dataclass(frozen=True)
class Task:
    id: UUID
    scope: Scope
    version: int
    fence: UUID
    question: str = ""


def split_content(units: list[tuple[int, str]], max_chunks: int = 5000) -> list[Chunk]:
    chunks: list[Chunk] = []
    for unit, content in units:
        start = 0
        while start < len(content):
            end = min(start + 1500, len(content))
            if content[start:end].strip():
                chunks.append(Chunk(len(chunks) + 1, unit, start, end, content[start:end]))
            if len(chunks) > max_chunks:
                raise RagFailure("INDEX_TOO_LARGE", "资料超过当前索引上限，请拆分后重试")
            if end == len(content):
                break
            start = end - 180
    if not chunks:
        raise RagFailure("NO_INDEXABLE_TEXT", "资料没有可检索的正文")
    return chunks


def validate_vectors(vectors: list[list[float]], count: int) -> None:
    if len(vectors) != count or any(
        len(vector) != DIMENSIONS
        or not all(math.isfinite(value) for value in vector)
        or sum(value * value for value in vector) == 0
        for vector in vectors
    ):
        raise RagFailure("EMBEDDING_INVALID", "向量服务返回格式不正确，请稍后重试")


def validate_answer(payload: dict[str, Any], sources: list[Evidence]) -> dict[str, Any]:
    """Only publish claims with quoted text that resolves to the retrieved version."""
    invalid = RagFailure("ANSWER_INVALID", "回答未通过来源校验，请重新提问或缩小问题范围")
    if payload.get("insufficient_evidence") is True:
        return {"insufficient_evidence": True, "claims": []}
    claims = payload.get("claims")
    if payload.get("insufficient_evidence") is not False or not isinstance(claims, list):
        raise invalid
    if not 1 <= len(claims) <= 8:
        raise invalid
    available = {str(source.id): source for source in sources}
    validated: list[dict[str, Any]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            raise invalid
        text = claim.get("text")
        citations = claim.get("citations")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise invalid
        if not isinstance(citations, list) or not 1 <= len(citations) <= 4:
            raise invalid
        checked: list[dict[str, Any]] = []
        for citation in citations:
            if not isinstance(citation, dict):
                raise invalid
            source = available.get(str(citation.get("source_id")))
            quote = citation.get("quote")
            if (
                source is None
                or not isinstance(quote, str)
                or not 8 <= len(quote) <= 800
                or quote not in source.content
            ):
                raise invalid
            checked.append(
                {
                    "source_id": str(source.id),
                    "quote": quote,
                    "unit": source.unit,
                    "locator": source.locator,
                    **(
                        {"document_id": str(source.document_id), "version_id": source.version_id}
                        if source.document_id is not None
                        else {}
                    ),
                }
            )
        validated.append({"text": text.strip(), "citations": checked})
    return {"insufficient_evidence": False, "claims": validated}


def validate_study_answer(
    payload: dict[str, Any],
    sources: list[Evidence],
    *,
    require_explanation: bool = False,
    require_sections: bool = False,
) -> dict[str, Any]:
    """Validate per-point teaching and sources while accepting historical flat answers."""
    answer = validate_answer(payload, sources)
    if answer["insufficient_evidence"]:
        return answer
    section_length = 0
    for raw, claim in zip(payload["claims"], answer["claims"], strict=True):
        title, teaching = raw.get("title"), raw.get("explanation")
        if title is None and teaching is None and not require_sections:
            continue
        if (
            not isinstance(title, str)
            or not 1 <= len(title.strip()) <= 120
            or not isinstance(teaching, str)
            or not 1 <= len(teaching.strip()) <= 4000
        ):
            raise RagFailure("ANSWER_INVALID", "知识点讲解格式错误，请重试")
        section_length += len(teaching.strip())
        claim.update(title=title.strip(), explanation=teaching.strip())
    if section_length > 4000:
        raise RagFailure("ANSWER_INVALID", "知识点讲解超出长度限制，请重试")
    explanation = payload.get("explanation")
    if explanation is None:
        # Results from an older in-flight model call still have cited claims only.
        if require_explanation:
            raise RagFailure("ANSWER_INVALID", "讲解内容缺失，请重试")
        return answer
    if not isinstance(explanation, str) or not 1 <= len(explanation.strip()) <= 4000:
        raise RagFailure("ANSWER_INVALID", "讲解内容格式错误，请重试")
    return {**answer, "explanation": explanation.strip()}
