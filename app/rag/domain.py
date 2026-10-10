from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

DIMENSIONS = 1024
CHUNK_VERSION = "source-window-1500-180-v1"
PROMPT_VERSION = "cited-claims-v1"
STUDY_PROMPT_VERSION = "pdf-knowledge-sections-v4"
RETRIEVAL_VERSION = "exact-cosine-cjk-fts-rrf-v2"
ANSWER_VALIDATION_REASONS = frozenset(
    {
        "generation_truncated",
        "generation_invalid_json",
        "generation_invalid_shape",
        "answer_shape",
        "point_count",
        "point_shape",
        "point_text",
        "citation_count",
        "citation_shape",
        "citation_source_unknown",
        "citation_quote_length",
        "citation_quote_not_exact",
        "point_teaching_shape",
        "teaching_total_length",
        "teaching_missing",
        "teaching_shape",
        "focused_point_limit",
    }
)


class RagFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AnswerValidationFailure(RagFailure):
    """Safe diagnosis: no document text, model output or provider messages."""

    def __init__(
        self,
        reason: str,
        *,
        point: int | None = None,
        citation: int | None = None,
        length: int | None = None,
    ) -> None:
        if reason not in ANSWER_VALIDATION_REASONS:
            raise ValueError("Unknown answer validation reason")
        super().__init__("ANSWER_INVALID", "回答未通过来源或格式校验，请重试")
        self.reason, self.point, self.citation, self.length = reason, point, citation, length

    def diagnosis(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "reason": self.reason,
                "point": self.point,
                "citation": self.citation,
                "length": self.length,
            }.items()
            if value is not None
        }


def canonical_excerpt(quote: str, source: str) -> str | None:
    """Resolve whitespace-only changes to a unique, exact original substring.

    No punctuation, word, case or number can change. The returned text still
    passes the ordinary exact-quote validator; ambiguous matches are rejected.
    """
    if quote in source:
        return quote
    normalized = re.sub(r"\s+", " ", quote).strip()
    if not normalized:
        return None
    characters: list[str] = []
    offsets: list[int] = []
    for position, character in enumerate(source):
        if character.isspace():
            if not characters or characters[-1] == " ":
                continue
            character = " "
        characters.append(character)
        offsets.append(position)
    plain = "".join(characters)
    start = plain.find(normalized)
    if start < 0 or plain.find(normalized, start + 1) >= 0:
        return None
    end = start + len(normalized) - 1
    return source[offsets[start] : offsets[end] + 1]


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


def study_quote_options(content: str) -> dict[str, str]:
    """Partition evidence into numbered, exact excerpts without changing its text."""
    excerpts: list[str] = []
    start = 0
    while start < len(content):
        end = min(start + 600, len(content))
        if end < len(content):
            boundary = content.rfind("\n", start + 300, end)
            if boundary < 0:
                boundary = content.rfind(" ", start + 300, end)
            if boundary >= 0:
                end = boundary + 1
        excerpt = content[start:end]
        if len(excerpt) < 8:
            if excerpts:
                excerpts[-1] += excerpt
            break
        excerpts.append(excerpt)
        start = end
    return {f"q{ordinal}": excerpt for ordinal, excerpt in enumerate(excerpts, 1)}


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
    generation_error = payload.get("_generation_error")
    if isinstance(generation_error, str) and generation_error in {
        "truncated",
        "invalid_json",
        "invalid_shape",
    }:
        raise AnswerValidationFailure(f"generation_{generation_error}")
    if payload.get("insufficient_evidence") is True:
        return {"insufficient_evidence": True, "claims": []}
    claims = payload.get("claims")
    if payload.get("insufficient_evidence") is not False or not isinstance(claims, list):
        raise AnswerValidationFailure("answer_shape")
    if not 1 <= len(claims) <= 8:
        raise AnswerValidationFailure("point_count", length=len(claims))
    available = {str(source.id): source for source in sources}
    validated: list[dict[str, Any]] = []
    for point, claim in enumerate(claims, 1):
        if not isinstance(claim, dict):
            raise AnswerValidationFailure("point_shape", point=point)
        text = claim.get("text")
        citations = claim.get("citations")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise AnswerValidationFailure(
                "point_text", point=point, length=len(text) if isinstance(text, str) else None
            )
        if not isinstance(citations, list) or not 1 <= len(citations) <= 4:
            raise AnswerValidationFailure(
                "citation_count",
                point=point,
                length=len(citations) if isinstance(citations, list) else None,
            )
        checked: list[dict[str, Any]] = []
        for position, citation in enumerate(citations, 1):
            if not isinstance(citation, dict):
                raise AnswerValidationFailure("citation_shape", point=point, citation=position)
            source = available.get(str(citation.get("source_id")))
            quote = citation.get("quote")
            if source is None:
                raise AnswerValidationFailure(
                    "citation_source_unknown", point=point, citation=position
                )
            if not isinstance(quote, str) or not 8 <= len(quote) <= 800:
                raise AnswerValidationFailure(
                    "citation_quote_length",
                    point=point,
                    citation=position,
                    length=len(quote) if isinstance(quote, str) else None,
                )
            if quote not in source.content:
                raise AnswerValidationFailure(
                    "citation_quote_not_exact", point=point, citation=position
                )
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
    for point, (raw, claim) in enumerate(zip(payload["claims"], answer["claims"], strict=True), 1):
        title, teaching = raw.get("title"), raw.get("explanation")
        if title is None and teaching is None and not require_sections:
            continue
        if (
            not isinstance(title, str)
            or not 1 <= len(title.strip()) <= 120
            or not isinstance(teaching, str)
            or not 1 <= len(teaching.strip()) <= 4000
        ):
            raise AnswerValidationFailure("point_teaching_shape", point=point)
        section_length += len(teaching.strip())
        claim.update(title=title.strip(), explanation=teaching.strip())
    if section_length > 4000:
        raise AnswerValidationFailure("teaching_total_length", length=section_length)
    explanation = payload.get("explanation")
    if explanation is None:
        # Results from an older in-flight model call still have cited claims only.
        if require_explanation:
            raise AnswerValidationFailure("teaching_missing")
        return answer
    if not isinstance(explanation, str) or not 1 <= len(explanation.strip()) <= 4000:
        raise AnswerValidationFailure("teaching_shape")
    return {**answer, "explanation": explanation.strip()}
