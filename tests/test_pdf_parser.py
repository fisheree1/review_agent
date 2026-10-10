from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from app.documents.application.errors import DocumentProcessingError
from app.documents.infrastructure import pdf_extractor
from app.documents.infrastructure.pdf_parser import PypdfDocumentParser
from tests.pdf_factory import write_text_pdf


def add_image(path: Path, *, size: int) -> None:
    writer = PdfWriter(clone_from=path)
    page = writer.pages[0]
    image = DecodedStreamObject()
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    image.set_data(b"\x00\x00\x00")
    page["/Resources"][NameObject("/XObject")] = DictionaryObject(
        {
            NameObject("/Diagram"): writer._add_object(image)  # noqa: SLF001
        }
    )
    content = page.get_contents()
    assert content is not None
    content.set_data(content.get_data() + f"\nq {size} 0 0 {size} 0 0 cm /Diagram Do Q".encode())
    page[NameObject("/Contents")] = writer._add_object(content)  # noqa: SLF001
    with path.open("wb") as stream:
        writer.write(stream)


def test_mixed_slide_retains_native_text_and_extracts_large_diagram_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "mixed.pdf"
    write_text_pdf(path, text="Learning models and their applications")
    add_image(path, size=500)
    monkeypatch.setattr(
        pdf_extractor, "_ocr_page", lambda _path, _page: "Classification and clustering"
    )
    result = pdf_extractor.extract_pdf(path, max_pages=10, max_characters=1000)
    text = result["contents"][0]["content"]
    assert "Learning models" in text and "Classification and clustering" in text
    assert result["contents"][0]["locator"]["position"] == 1
    assert "layout-ocr-v2" in result["parser_version"]


def test_small_logo_does_not_require_ocr_for_a_text_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "logo.pdf"
    write_text_pdf(path, text="Learning models and their applications")
    add_image(path, size=20)

    def unavailable(_path: Path, _page: int) -> str:
        raise FileNotFoundError("tesseract")

    monkeypatch.setattr(pdf_extractor, "_ocr_page", unavailable)
    result = pdf_extractor.extract_pdf(path, max_pages=10, max_characters=1000)
    assert result["parser_name"] == "pypdf"


def test_sparse_ocr_keeps_multiline_labels_and_spatial_location() -> None:
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
    )
    tsv += "1\t1\t0\t0\t0\t0\t0\t0\t1000\t500\t-1\t\n"
    tsv += "5\t1\t1\t1\t1\t1\t100\t50\t60\t20\t90\tNaive\n"
    tsv += "5\t1\t1\t1\t2\t1\t100\t70\t60\t20\t90\tBayes\n"
    text = pdf_extractor._spatial_ocr(tsv)
    assert "left=10% top=10%" in text
    assert "Naive\nBayes" in text


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
