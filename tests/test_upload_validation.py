from __future__ import annotations

from io import BytesIO

import pytest

from app.core.errors import ApplicationError
from app.documents.application.upload_validation import stage_pdf_upload


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
async def test_upload_accepts_a_pdf() -> None:
    source = MemoryUpload(
        b"%PDF-1.7\n",
        filename="review.pdf",
        content_type="application/pdf",
    )

    staged = await stage_pdf_upload(source, max_bytes=1024)
    try:
        assert staged.media_type == "application/pdf"
        assert staged.path.suffix == ".pdf"
    finally:
        staged.path.unlink(missing_ok=True)


@pytest.mark.anyio
@pytest.mark.parametrize("extension", ["docx", "pptx"])
async def test_upload_rejects_office_files(extension: str) -> None:
    source = MemoryUpload(
        b"%PDF-1.7\n",
        filename=f"notes.{extension}",
        content_type="application/octet-stream",
    )

    with pytest.raises(ApplicationError) as error:
        await stage_pdf_upload(source, max_bytes=1024)

    assert error.value.code == "UNSUPPORTED_FILE_TYPE"
    assert error.value.status_code == 415
