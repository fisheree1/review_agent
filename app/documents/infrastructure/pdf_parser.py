from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.documents.application.errors import DocumentProcessingError
from app.documents.domain.entities import DocumentPage, ParsedDocument


class PypdfDocumentParser:
    def __init__(
        self,
        *,
        timeout_seconds: int,
        max_pages: int,
        max_characters: int = 5_000_000,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_pages = max_pages
        self._max_characters = max_characters

    async def parse(self, source_path: Path) -> ParsedDocument:
        descriptor, result_name = tempfile.mkstemp(
            prefix="review-agent-pdf-result-", suffix=".json"
        )
        os.close(descriptor)
        result_path = Path(result_name)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.documents.infrastructure.pdf_extractor",
            str(source_path),
            str(result_path),
            str(self._max_pages),
            str(self._max_characters),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            try:
                await asyncio.wait_for(process.wait(), timeout=self._timeout_seconds)
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise DocumentProcessingError(
                    code="PDF_PARSE_TIMEOUT",
                    message="PDF 解析超时，请缩小文件后重试",
                ) from exc

            if process.returncode != 0:
                raise DocumentProcessingError(
                    code="PDF_PROCESSOR_FAILED",
                    message="PDF 解析器异常退出，请重试",
                )
            return await asyncio.to_thread(self._read_result, result_path)
        finally:
            await asyncio.to_thread(result_path.unlink, missing_ok=True)

    @staticmethod
    def _read_result(result_path: Path) -> ParsedDocument:
        try:
            payload: Any = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DocumentProcessingError(
                code="PDF_PROCESSOR_OUTPUT_INVALID",
                message="PDF 解析结果无效，请重试",
            ) from exc

        if not isinstance(payload, dict):
            raise DocumentProcessingError(
                code="PDF_PROCESSOR_OUTPUT_INVALID",
                message="PDF 解析结果无效，请重试",
            )
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            if isinstance(code, str) and isinstance(message, str):
                raise DocumentProcessingError(code=code, message=message)

        parser_name = payload.get("parser_name")
        parser_version = payload.get("parser_version")
        raw_pages = payload.get("pages")
        if not isinstance(parser_name, str) or not isinstance(parser_version, str):
            raise DocumentProcessingError(
                code="PDF_PROCESSOR_OUTPUT_INVALID",
                message="PDF 解析结果无效，请重试",
            )
        if not isinstance(raw_pages, list):
            raise DocumentProcessingError(
                code="PDF_PROCESSOR_OUTPUT_INVALID",
                message="PDF 解析结果无效，请重试",
            )

        pages: list[DocumentPage] = []
        for expected_number, raw_page in enumerate(raw_pages, start=1):
            if not isinstance(raw_page, dict):
                raise DocumentProcessingError(
                    code="PDF_PROCESSOR_OUTPUT_INVALID",
                    message="PDF 解析结果无效，请重试",
                )
            page_number = raw_page.get("page_number")
            content = raw_page.get("content")
            if page_number != expected_number or not isinstance(content, str):
                raise DocumentProcessingError(
                    code="PDF_PROCESSOR_OUTPUT_INVALID",
                    message="PDF 解析结果无效，请重试",
                )
            pages.append(DocumentPage(page_number=page_number, content=content))
        return ParsedDocument(
            pages=tuple(pages),
            parser_name=parser_name,
            parser_version=parser_version,
        )
