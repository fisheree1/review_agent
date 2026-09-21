from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.documents.application.errors import DocumentProcessingError
from app.documents.domain.entities import (
    CitationLocator,
    CitationLocatorKind,
    DocumentContent,
    ParsedDocument,
)


def read_parser_result(
    result_path: Path,
    *,
    invalid_code: str,
    invalid_message: str,
) -> ParsedDocument:
    try:
        payload: Any = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DocumentProcessingError(code=invalid_code, message=invalid_message) from exc
    if not isinstance(payload, dict):
        raise DocumentProcessingError(code=invalid_code, message=invalid_message)
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        if isinstance(code, str) and isinstance(message, str):
            raise DocumentProcessingError(code=code, message=message)

    parser_name = payload.get("parser_name")
    parser_version = payload.get("parser_version")
    raw_contents = payload.get("contents")
    if (
        not isinstance(parser_name, str)
        or not isinstance(parser_version, str)
        or not isinstance(raw_contents, list)
    ):
        raise DocumentProcessingError(code=invalid_code, message=invalid_message)

    contents: list[DocumentContent] = []
    for expected_ordinal, raw_content in enumerate(raw_contents, start=1):
        if not isinstance(raw_content, dict):
            raise DocumentProcessingError(code=invalid_code, message=invalid_message)
        raw_locator = raw_content.get("locator")
        content = raw_content.get("content")
        if (
            raw_content.get("ordinal") != expected_ordinal
            or not isinstance(content, str)
            or not isinstance(raw_locator, dict)
        ):
            raise DocumentProcessingError(code=invalid_code, message=invalid_message)
        raw_kind = raw_locator.get("kind")
        if not isinstance(raw_kind, str):
            raise DocumentProcessingError(code=invalid_code, message=invalid_message)
        try:
            kind = CitationLocatorKind(raw_kind)
        except (ValueError, TypeError) as exc:
            raise DocumentProcessingError(code=invalid_code, message=invalid_message) from exc
        position = raw_locator.get("position")
        title = raw_locator.get("title")
        path = raw_locator.get("path", [])
        if (
            not isinstance(position, int)
            or position <= 0
            or (title is not None and not isinstance(title, str))
            or not isinstance(path, list)
            or not all(isinstance(item, str) for item in path)
            or (isinstance(title, str) and len(title) > 300)
            or any(len(item) > 300 for item in path)
        ):
            raise DocumentProcessingError(code=invalid_code, message=invalid_message)
        contents.append(
            DocumentContent(
                ordinal=expected_ordinal,
                content=content,
                locator=CitationLocator(
                    kind=kind,
                    position=position,
                    title=title,
                    path=tuple(path),
                ),
            )
        )
    return ParsedDocument(
        contents=tuple(contents),
        parser_name=parser_name,
        parser_version=parser_version,
    )
