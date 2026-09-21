from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from app.documents.application.errors import DocumentProcessingError
from app.documents.application.upload_validation import DOCX_MEDIA_TYPE, PPTX_MEDIA_TYPE
from app.documents.domain.entities import ParsedDocument
from app.documents.infrastructure.parser_output import read_parser_result


class OoxmlDocumentParser:
    def __init__(
        self,
        *,
        timeout_seconds: int,
        max_units: int,
        max_characters: int,
        max_uncompressed_bytes: int,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_units = max_units
        self._max_characters = max_characters
        self._max_uncompressed_bytes = max_uncompressed_bytes

    async def parse(self, source_path: Path, *, media_type: str) -> ParsedDocument:
        if media_type not in {DOCX_MEDIA_TYPE, PPTX_MEDIA_TYPE}:
            raise DocumentProcessingError(
                code="UNSUPPORTED_DOCUMENT_TYPE",
                message="解析器不支持该文件类型",
            )
        descriptor, result_name = tempfile.mkstemp(
            prefix="review-agent-office-result-", suffix=".json"
        )
        os.close(descriptor)
        result_path = Path(result_name)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.documents.infrastructure.office_extractor",
            str(source_path),
            str(result_path),
            media_type,
            str(self._max_units),
            str(self._max_characters),
            str(self._max_uncompressed_bytes),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={
                "PATH": os.environ.get("PATH", os.defpath),
                "PYTHONPATH": os.pathsep.join(path for path in sys.path if path),
                "PYTHONUTF8": "1",
            },
        )
        try:
            try:
                await asyncio.wait_for(process.wait(), timeout=self._timeout_seconds)
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise DocumentProcessingError(
                    code="OFFICE_PARSE_TIMEOUT",
                    message="Office 文档解析超时，请缩小文件后重试",
                ) from exc
            if process.returncode != 0:
                raise DocumentProcessingError(
                    code="OFFICE_PROCESSOR_FAILED",
                    message="Office 文档解析器异常退出，请重试",
                )
            return await asyncio.to_thread(
                read_parser_result,
                result_path,
                invalid_code="OFFICE_PROCESSOR_OUTPUT_INVALID",
                invalid_message="Office 文档解析结果无效，请重试",
            )
        finally:
            await asyncio.to_thread(result_path.unlink, missing_ok=True)
