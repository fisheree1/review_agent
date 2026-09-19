from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from app.documents.application.errors import DocumentProcessingError, StorageOperationError
from app.documents.application.ports import DocumentParser, DocumentStorage, UnitOfWorkFactory
from app.documents.domain.entities import ClaimedJob


class DocumentJobProcessor:
    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        storage: DocumentStorage,
        parser: DocumentParser,
        lease_seconds: int,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._storage = storage
        self._parser = parser
        self._lease_seconds = lease_seconds

    async def process_next(self) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.documents.claim_next_job(lease_seconds=self._lease_seconds)
            await unit_of_work.commit()
        if job is None:
            return False

        descriptor, temporary_name = tempfile.mkstemp(prefix="review-agent-worker-", suffix=".pdf")
        os.close(descriptor)
        source_path = Path(temporary_name)
        try:
            await self._storage.download(
                object_key=job.object_key,
                destination_path=source_path,
            )
            parsed = await self._parser.parse(source_path)
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.documents.complete_job(job=job, parsed=parsed)
                await unit_of_work.commit()
        except DocumentProcessingError as exc:
            await self._fail(job=job, code=exc.code, message=exc.message)
        except StorageOperationError:
            await self._fail(
                job=job,
                code="SOURCE_FILE_UNAVAILABLE",
                message="无法读取已保存的原文件，请重新上传",
            )
        except Exception:
            await self._fail(
                job=job,
                code="DOCUMENT_PROCESSING_FAILED",
                message="PDF 处理失败，请重试",
            )
        finally:
            await asyncio.to_thread(source_path.unlink, missing_ok=True)
        return True

    async def _fail(self, *, job: ClaimedJob, code: str, message: str) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.documents.fail_job(
                job=job,
                failure_code=code,
                failure_message=message,
            )
            await unit_of_work.commit()
