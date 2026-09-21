from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath
from xml.etree import ElementTree as ET

from app.core.errors import ApplicationError
from app.documents.application.ports import UploadSource

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
OCTET_STREAM = "application/octet-stream"
READ_SIZE = 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 200
MAX_CONTENT_TYPES_BYTES = 1024 * 1024

SUPPORTED_TYPES = {
    ".pdf": PDF_MEDIA_TYPE,
    ".docx": DOCX_MEDIA_TYPE,
    ".pptx": PPTX_MEDIA_TYPE,
}
OOXML_MARKERS = {
    DOCX_MEDIA_TYPE: "word/document.xml",
    PPTX_MEDIA_TYPE: "ppt/presentation.xml",
}
OOXML_MAIN_CONTENT_TYPES = {
    DOCX_MEDIA_TYPE: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    ),
    PPTX_MEDIA_TYPE: (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
    ),
}


@dataclass(frozen=True, slots=True)
class StagedUpload:
    path: Path
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str


def _safe_display_filename(filename: str | None) -> tuple[str, str]:
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
    suffix = Path(display_name).suffix.lower()
    if suffix not in SUPPORTED_TYPES:
        raise ApplicationError(
            code="UNSUPPORTED_FILE_TYPE",
            message="当前支持 PDF、DOCX 和 PPTX 文件",
            status_code=415,
        )
    return display_name, suffix


def _validate_ooxml(path: Path, *, expected_media_type: str) -> None:
    marker = OOXML_MARKERS[expected_media_type]
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise ValueError("too many archive entries")
            total_uncompressed = 0
            names: set[str] = set()
            for entry in entries:
                normalized = PurePosixPath(entry.filename)
                if normalized.is_absolute() or ".." in normalized.parts or entry.flag_bits & 0x1:
                    raise ValueError("unsafe archive member")
                total_uncompressed += entry.file_size
                if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise ValueError("archive expands beyond limit")
                if (
                    entry.compress_size > 0
                    and entry.file_size / entry.compress_size > MAX_ARCHIVE_COMPRESSION_RATIO
                ):
                    raise ValueError("suspicious archive compression ratio")
                names.add(entry.filename)
            if marker not in names or "[Content_Types].xml" not in names:
                raise ValueError("required OOXML parts are missing")
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise ValueError("macro-enabled Office files are not supported")
            if archive.getinfo("[Content_Types].xml").file_size > MAX_CONTENT_TYPES_BYTES:
                raise ValueError("OOXML content type manifest is too large")
            raw_content_types = archive.read("[Content_Types].xml")
            lowered = raw_content_types.lower()
            if b"<!doctype" in lowered or b"<!entity" in lowered:
                raise ValueError("unsafe XML declaration")
            content_types = ET.fromstring(raw_content_types)
            expected_part = f"/{marker}"
            expected_content_type = OOXML_MAIN_CONTENT_TYPES[expected_media_type]
            if not any(
                override.tag.rsplit("}", 1)[-1] == "Override"
                and override.get("PartName") == expected_part
                and override.get("ContentType") == expected_content_type
                for override in content_types
            ):
                raise ValueError("OOXML main content type does not match the extension")
    except (OSError, ET.ParseError, zipfile.BadZipFile, ValueError) as exc:
        kind = "DOCX" if expected_media_type == DOCX_MEDIA_TYPE else "PPTX"
        raise ApplicationError(
            code=f"INVALID_{kind}_PACKAGE",
            message=f"文件内容不是安全、有效的 {kind} 文档",
            status_code=415,
        ) from exc


async def stage_document_upload(source: UploadSource, *, max_bytes: int) -> StagedUpload:
    display_name, suffix = _safe_display_filename(source.filename)
    expected_media_type = SUPPORTED_TYPES[suffix]
    supplied_media_type = (source.content_type or "").split(";", 1)[0].strip().lower()
    if supplied_media_type not in {"", OCTET_STREAM, expected_media_type}:
        raise ApplicationError(
            code="INVALID_MEDIA_TYPE",
            message="文件扩展名、媒体类型与内容不一致",
            status_code=415,
        )

    descriptor, temporary_name = tempfile.mkstemp(prefix="review-agent-upload-", suffix=suffix)
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
                        message=f"文件大小不能超过 {max_bytes // (1024 * 1024)} MB",
                        status_code=413,
                    )
                if len(first_bytes) < 5:
                    first_bytes = (first_bytes + chunk)[:5]
                digest.update(chunk)
                staged_file.write(chunk)

        if byte_size == 0:
            raise ApplicationError(code="EMPTY_FILE", message="上传的文件为空", status_code=422)
        if expected_media_type == PDF_MEDIA_TYPE:
            if first_bytes != b"%PDF-":
                raise ApplicationError(
                    code="INVALID_PDF_SIGNATURE",
                    message="文件内容不是有效的 PDF",
                    status_code=415,
                )
        else:
            await asyncio.to_thread(
                _validate_ooxml,
                path,
                expected_media_type=expected_media_type,
            )

        return StagedUpload(
            path=path,
            original_filename=display_name,
            media_type=expected_media_type,
            byte_size=byte_size,
            sha256=digest.hexdigest(),
        )
    except BaseException:
        await asyncio.to_thread(path.unlink, missing_ok=True)
        raise


# Kept for callers upgrading from the PDF-only API.
stage_pdf_upload = stage_document_upload
