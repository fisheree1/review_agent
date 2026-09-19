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
