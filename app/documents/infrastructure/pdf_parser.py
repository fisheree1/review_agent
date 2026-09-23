from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
from pathlib import Path

from app.documents.application.errors import DocumentProcessingError
from app.documents.application.upload_validation import PDF_MEDIA_TYPE
from app.documents.domain.entities import ParsedDocument
from app.documents.infrastructure.parser_output import read_parser_result


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

    async def parse(self, source_path: Path, *, media_type: str = PDF_MEDIA_TYPE) -> ParsedDocument:
        if media_type != PDF_MEDIA_TYPE:
            raise DocumentProcessingError(
                code="UNSUPPORTED_DOCUMENT_TYPE",
                message="解析器不支持该文件类型",
            )
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
            env={
                "PATH": os.environ.get("PATH", os.defpath),
                "PYTHONPATH": os.pathsep.join(path for path in sys.path if path),
                "PYTHONUTF8": "1",
            },
            start_new_session=True,
        )
        try:
            try:
                await asyncio.wait_for(process.wait(), timeout=self._timeout_seconds)
            except TimeoutError as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
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
            return await asyncio.to_thread(
                read_parser_result,
                result_path,
                invalid_code="PDF_PROCESSOR_OUTPUT_INVALID",
                invalid_message="PDF 解析结果无效，请重试",
            )
        finally:
            await asyncio.to_thread(result_path.unlink, missing_ok=True)
