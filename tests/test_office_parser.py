from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.documents.application.upload_validation import DOCX_MEDIA_TYPE, PPTX_MEDIA_TYPE
from app.documents.infrastructure.office_parser import OoxmlDocumentParser

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _parser() -> OoxmlDocumentParser:
    return OoxmlDocumentParser(
        timeout_seconds=5,
        max_units=20,
        max_characters=10_000,
        max_uncompressed_bytes=1024 * 1024,
    )


def _write_docx(path: Path) -> None:
    document = f"""
    <w:document xmlns:w="{W}"><w:body>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Core idea</w:t></w:r></w:p>
      <w:p><w:r><w:t>Evidence under the heading.</w:t></w:r></w:p>
      <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Detail</w:t></w:r></w:p>
      <w:p><w:r><w:t>Nested evidence.</w:t></w:r></w:p>
    </w:body></w:document>
    """
    styles = f"""
    <w:styles xmlns:w="{W}">
      <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
      <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>
    </w:styles>
    """
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)


def _write_pptx(path: Path) -> None:
    presentation = f"""
    <p:presentation xmlns:p="{P}" xmlns:r="{R}">
      <p:sldIdLst><p:sldId id="256" r:id="slide-one"/></p:sldIdLst>
    </p:presentation>
    """
    presentation_rels = f"""
    <Relationships xmlns="{REL}">
      <Relationship Id="slide-one" Type="{R}/slide" Target="/ppt/slides/slide1.xml"/>
    </Relationships>
    """
    slide = f"""
    <p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>
      <p:sp><p:txBody><a:p><a:r><a:t>Slide title</a:t></a:r></a:p></p:txBody></p:sp>
      <p:sp><p:txBody><a:p><a:r><a:t>Visible evidence</a:t></a:r></a:p></p:txBody></p:sp>
    </p:spTree></p:cSld></p:sld>
    """
    slide_rels = f"""
    <Relationships xmlns="{REL}">
      <Relationship Id="notes" Type="{R}/notesSlide" Target="../notesSlides/notesSlide1.xml"/>
    </Relationships>
    """
    notes = f"""
    <p:notes xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp>
      <p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>
      <p:txBody><a:p><a:r><a:t>Speaker evidence</a:t></a:r></a:p></p:txBody>
    </p:sp></p:spTree></p:cSld></p:notes>
    """
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
        archive.writestr("ppt/notesSlides/notesSlide1.xml", notes)


@pytest.mark.anyio
async def test_docx_preserves_heading_hierarchy_as_citation_locator(tmp_path: Path) -> None:
    path = tmp_path / "review.docx"
    _write_docx(path)

    parsed = await _parser().parse(path, media_type=DOCX_MEDIA_TYPE)

    assert [content.locator.title for content in parsed.contents] == ["Core idea", "Detail"]
    assert parsed.contents[1].locator.path == ("Core idea", "Detail")
    assert "Nested evidence" in parsed.contents[1].content


@pytest.mark.anyio
async def test_pptx_preserves_slide_number_title_and_notes(tmp_path: Path) -> None:
    path = tmp_path / "slides.pptx"
    _write_pptx(path)

    parsed = await _parser().parse(path, media_type=PPTX_MEDIA_TYPE)

    assert len(parsed.contents) == 1
    assert parsed.contents[0].locator.kind == "slide"
    assert parsed.contents[0].locator.position == 1
    assert parsed.contents[0].locator.title == "Slide title"
    assert "Visible evidence" in parsed.contents[0].content
    assert "Speaker evidence" in parsed.contents[0].content
