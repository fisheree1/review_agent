from __future__ import annotations

from pathlib import Path

from app.documents.application.errors import DocumentProcessingError
from app.documents.application.ports import DocumentParser
from app.documents.application.upload_validation import (
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    PPTX_MEDIA_TYPE,
)
from app.documents.domain.entities import ParsedDocument


class DocumentParserRegistry:
    def __init__(self, *, pdf_parser: DocumentParser, office_parser: DocumentParser) -> None:
        self._parsers = {
            PDF_MEDIA_TYPE: pdf_parser,
            DOCX_MEDIA_TYPE: office_parser,
            PPTX_MEDIA_TYPE: office_parser,
        }

    async def parse(self, source_path: Path, *, media_type: str) -> ParsedDocument:
        parser = self._parsers.get(media_type)
        if parser is None:
            raise DocumentProcessingError(
                code="UNSUPPORTED_DOCUMENT_TYPE",
                message="暂不支持该文件类型",
            )
        return await parser.parse(source_path, media_type=media_type)
