from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from app.core.errors import ApplicationError
from app.documents.application.upload_validation import (
    DOCX_MEDIA_TYPE,
    stage_document_upload,
    stage_pdf_upload,
)


class MemoryUpload:
    def __init__(self, content: bytes, *, filename: str, content_type: str) -> None:
        self.filename = filename
        self.content_type = content_type
        self._content = BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._content.read(size)


@pytest.mark.anyio
async def test_upload_rejects_spoofed_pdf_signature() -> None:
    source = MemoryUpload(
        b"not a pdf",
        filename="notes.pdf",
        content_type="application/pdf",
    )

    with pytest.raises(ApplicationError) as error:
        await stage_pdf_upload(source, max_bytes=1024)

    assert error.value.code == "INVALID_PDF_SIGNATURE"


@pytest.mark.anyio
async def test_upload_rejects_oversized_pdf() -> None:
    source = MemoryUpload(
        b"%PDF-" + (b"x" * 16),
        filename="notes.pdf",
        content_type="application/pdf",
    )

    with pytest.raises(ApplicationError) as error:
        await stage_pdf_upload(source, max_bytes=10)

    assert error.value.code == "FILE_TOO_LARGE"


@pytest.mark.anyio
async def test_upload_accepts_a_structurally_valid_docx() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types><Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr("word/document.xml", "<document />")
    source = MemoryUpload(
        buffer.getvalue(),
        filename="review.docx",
        content_type=DOCX_MEDIA_TYPE,
    )

    staged = await stage_document_upload(source, max_bytes=1024)
    try:
        assert staged.media_type == DOCX_MEDIA_TYPE
        assert staged.path.suffix == ".docx"
    finally:
        staged.path.unlink(missing_ok=True)


@pytest.mark.anyio
async def test_upload_rejects_a_renamed_zip_without_docx_parts() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("notes.txt", "not a Word document")
    source = MemoryUpload(
        buffer.getvalue(),
        filename="spoofed.docx",
        content_type=DOCX_MEDIA_TYPE,
    )

    with pytest.raises(ApplicationError) as error:
        await stage_document_upload(source, max_bytes=1024)

    assert error.value.code == "INVALID_DOCX_PACKAGE"
