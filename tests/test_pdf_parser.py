from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.documents.application.errors import DocumentProcessingError
from app.documents.infrastructure.pdf_parser import PypdfDocumentParser
from tests.pdf_factory import write_text_pdf


@pytest.mark.anyio
async def test_text_pdf_preserves_page_number_and_content(tmp_path: Path) -> None:
    pdf_path = tmp_path / "text.pdf"
    write_text_pdf(pdf_path, text="Important learning result")

    parsed = await PypdfDocumentParser(timeout_seconds=5, max_pages=10).parse(pdf_path)

    assert len(parsed.pages) == 1
    assert parsed.pages[0].page_number == 1
    assert parsed.pages[0].content == "Important learning result"


@pytest.mark.anyio
async def test_image_only_pdf_returns_explainable_failure(tmp_path: Path) -> None:
    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as output:
        writer.write(output)

    with pytest.raises(DocumentProcessingError) as error:
        await PypdfDocumentParser(timeout_seconds=5, max_pages=10).parse(pdf_path)

    assert error.value.code == "PDF_TEXT_NOT_FOUND"
    assert "OCR" in error.value.message


@pytest.mark.anyio
async def test_pdf_with_excessive_extracted_text_is_rejected(tmp_path: Path) -> None:
    pdf_path = tmp_path / "too-much-text.pdf"
    write_text_pdf(pdf_path, text="This text exceeds the configured limit")

    with pytest.raises(DocumentProcessingError) as error:
        await PypdfDocumentParser(
            timeout_seconds=5,
            max_pages=10,
            max_characters=10,
        ).parse(pdf_path)

    assert error.value.code == "PDF_TEXT_LIMIT_EXCEEDED"
