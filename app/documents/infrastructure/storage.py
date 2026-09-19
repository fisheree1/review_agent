from __future__ import annotations

import asyncio
from pathlib import Path

from minio import Minio

from app.documents.application.errors import StorageOperationError


class MinioDocumentStorage:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool,
    ) -> None:
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._bucket = bucket

    async def upload(self, *, object_key: str, source_path: Path, length: int) -> None:
        file_size = await asyncio.to_thread(lambda: source_path.stat().st_size)
        if file_size != length:
            raise StorageOperationError("Staged file size changed before upload")
        try:
            await asyncio.to_thread(
                self._client.fput_object,
                self._bucket,
                object_key,
                str(source_path),
                "application/pdf",
            )
        except StorageOperationError:
            raise
        except Exception as exc:
            raise StorageOperationError("Object upload failed") from exc

    async def download(self, *, object_key: str, destination_path: Path) -> None:
        try:
            await asyncio.to_thread(
                self._client.fget_object,
                self._bucket,
                object_key,
                str(destination_path),
            )
        except Exception as exc:
            raise StorageOperationError("Object download failed") from exc

    async def delete(self, *, object_key: str) -> None:
        try:
            await asyncio.to_thread(self._client.remove_object, self._bucket, object_key)
        except Exception as exc:
            raise StorageOperationError("Object deletion failed") from exc
