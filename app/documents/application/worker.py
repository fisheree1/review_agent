from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from app.documents.application.errors import DocumentProcessingError, StorageOperationError
from app.documents.application.ports import DocumentParser, DocumentStorage, UnitOfWorkFactory
from app.documents.domain.entities import ClaimedJob

logger = logging.getLogger(__name__)


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

        logger.info(
            "document_job_started job_id=%s document_id=%s",
            job.public_id,
            job.document_public_id,
        )

        suffix_by_media_type = {
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="review-agent-worker-",
            suffix=suffix_by_media_type.get(job.media_type, ".bin"),
        )
        os.close(descriptor)
        source_path = Path(temporary_name)
        try:
            await self._storage.download(
                object_key=job.object_key,
                destination_path=source_path,
            )
            parsed = await self._parser.parse(source_path, media_type=job.media_type)
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.documents.complete_job(job=job, parsed=parsed)
                await unit_of_work.commit()
            logger.info(
                "document_job_succeeded job_id=%s document_id=%s content_units=%s",
                job.public_id,
                job.document_public_id,
                len(parsed.contents),
            )
        except DocumentProcessingError as exc:
            logger.warning(
                "document_job_failed job_id=%s document_id=%s code=%s",
                job.public_id,
                job.document_public_id,
                exc.code,
            )
            await self._fail(job=job, code=exc.code, message=exc.message)
        except StorageOperationError:
            logger.warning(
                "document_job_failed job_id=%s document_id=%s code=SOURCE_FILE_UNAVAILABLE",
                job.public_id,
                job.document_public_id,
            )
            await self._fail(
                job=job,
                code="SOURCE_FILE_UNAVAILABLE",
                message="无法读取已保存的原文件，请重新上传",
            )
        except Exception as exc:
            logger.error(
                "document_job_failed job_id=%s document_id=%s "
                "code=DOCUMENT_PROCESSING_FAILED error_type=%s",
                job.public_id,
                job.document_public_id,
                type(exc).__name__,
            )
            await self._fail(
                job=job,
                code="DOCUMENT_PROCESSING_FAILED",
                message="文档处理失败，请重试",
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
