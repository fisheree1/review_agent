from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.documents.application.errors import DocumentProcessingError
from app.documents.infrastructure import pdf_extractor
from app.documents.infrastructure.pdf_parser import PypdfDocumentParser
from tests.pdf_factory import write_text_pdf


@pytest.mark.anyio
async def test_text_pdf_preserves_page_number_and_content(tmp_path: Path) -> None:
    pdf_path = tmp_path / "text.pdf"
    write_text_pdf(pdf_path, text="Important learning result")

    parsed = await PypdfDocumentParser(timeout_seconds=5, max_pages=10).parse(pdf_path)

    assert len(parsed.contents) == 1
    assert parsed.contents[0].ordinal == 1
    assert parsed.contents[0].locator.kind == "page"
    assert parsed.contents[0].locator.position == 1
    assert parsed.contents[0].content == "Important learning result"


def test_image_only_pdf_returns_explainable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as output:
        writer.write(output)

    monkeypatch.setattr(pdf_extractor, "_ocr_page", lambda _path, _page: "")
    result = pdf_extractor.extract_pdf(pdf_path, max_pages=10, max_characters=1000)

    assert result["error"]["code"] == "PDF_TEXT_NOT_FOUND"
    assert "扫描质量" in result["error"]["message"]


def test_scanned_page_uses_ocr_text_and_retains_page_locator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf_path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as output:
        writer.write(output)
    monkeypatch.setattr(
        pdf_extractor, "_ocr_page", lambda _path, _page: "Recognized learning notes"
    )
    result = pdf_extractor.extract_pdf(pdf_path, max_pages=10, max_characters=1000)

    assert result["parser_name"] == "pypdf+tesseract"
    assert result["contents"][0]["content"] == "Recognized learning notes"
    assert result["contents"][0]["locator"]["position"] == 1


def test_ocr_missing_binary_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf_path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as output:
        writer.write(output)

    def unavailable(_path: Path, _page: int) -> str:
        raise FileNotFoundError("tesseract")

    monkeypatch.setattr(pdf_extractor, "_ocr_page", unavailable)
    result = pdf_extractor.extract_pdf(pdf_path, max_pages=10, max_characters=1000)
    assert result["error"]["code"] == "PDF_OCR_UNAVAILABLE"


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
