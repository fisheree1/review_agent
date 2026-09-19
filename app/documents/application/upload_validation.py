from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePath

from app.core.errors import ApplicationError
from app.documents.application.ports import UploadSource

PDF_MEDIA_TYPE = "application/pdf"
READ_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class StagedUpload:
    path: Path
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str


def _safe_display_filename(filename: str | None) -> str:
    if filename is None:
        raise ApplicationError(
            code="FILENAME_REQUIRED",
            message="上传文件需要文件名",
            status_code=422,
        )
    display_name = PurePath(filename.replace("\\", "/")).name.strip()
    if not display_name or len(display_name) > 255:
        raise ApplicationError(
            code="INVALID_FILENAME",
            message="文件名为空或超过 255 个字符",
            status_code=422,
        )
    if Path(display_name).suffix.lower() != ".pdf":
        raise ApplicationError(
            code="UNSUPPORTED_FILE_TYPE",
            message="当前仅支持 PDF 文件",
            status_code=415,
        )
    return display_name


async def stage_pdf_upload(source: UploadSource, *, max_bytes: int) -> StagedUpload:
    display_name = _safe_display_filename(source.filename)
    media_type = (source.content_type or "").split(";", 1)[0].strip().lower()
    if media_type != PDF_MEDIA_TYPE:
        raise ApplicationError(
            code="INVALID_MEDIA_TYPE",
            message="文件的媒体类型必须是 application/pdf",
            status_code=415,
        )

    descriptor, temporary_name = tempfile.mkstemp(prefix="review-agent-upload-", suffix=".pdf")
    path = Path(temporary_name)
    digest = hashlib.sha256()
    byte_size = 0
    first_bytes = b""

    try:
        with os.fdopen(descriptor, "wb") as staged_file:
            while chunk := await source.read(READ_SIZE):
                byte_size += len(chunk)
                if byte_size > max_bytes:
                    raise ApplicationError(
                        code="FILE_TOO_LARGE",
                        message=f"PDF 大小不能超过 {max_bytes // (1024 * 1024)} MB",
                        status_code=413,
                    )
                if len(first_bytes) < 5:
                    first_bytes = (first_bytes + chunk)[:5]
                digest.update(chunk)
                staged_file.write(chunk)

        if byte_size == 0:
            raise ApplicationError(
                code="EMPTY_FILE",
                message="上传的 PDF 为空",
                status_code=422,
            )
        if first_bytes != b"%PDF-":
            raise ApplicationError(
                code="INVALID_PDF_SIGNATURE",
                message="文件内容不是有效的 PDF",
                status_code=415,
            )

        return StagedUpload(
            path=path,
            original_filename=display_name,
            media_type=PDF_MEDIA_TYPE,
            byte_size=byte_size,
            sha256=digest.hexdigest(),
        )
    except BaseException:
        await asyncio.to_thread(path.unlink, missing_ok=True)
        raise
