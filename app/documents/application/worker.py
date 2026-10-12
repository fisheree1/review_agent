from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from pathlib import Path

from app.documents.application.errors import DocumentProcessingError, StorageOperationError
from app.documents.application.ports import DocumentParser, DocumentStorage, UnitOfWorkFactory
from app.documents.domain.entities import ClaimedJob

logger = logging.getLogger(__name__)


class DocumentJobProcessor:
    """Process one claimed parse job while holding a renewed, fenced lease.

    The lease is renewed in the background for as long as the job runs, so parsing may
    legitimately take longer than one lease period. If renewal reports that another worker
    now owns the job (the attempt number changed), local work is cancelled and its result
    is discarded; the repository also rejects stale completions on its own.
    """

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        storage: DocumentStorage,
        parser: DocumentParser,
        lease_seconds: int,
        renew_interval_seconds: float | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._storage = storage
        self._parser = parser
        self._lease_seconds = lease_seconds
        self._renew_interval = (
            renew_interval_seconds
            if renew_interval_seconds is not None
            else max(1.0, lease_seconds / 3)
        )

    async def process_next(self) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.documents.claim_next_job(lease_seconds=self._lease_seconds)
            await unit_of_work.commit()
        if job is None:
            return False

        logger.info(
            "document_job_started job_id=%s document_id=%s attempt=%s",
            job.public_id,
            job.document_public_id,
            job.attempt,
        )
        work = asyncio.create_task(self._process(job))
        lease_lost = False

        async def keep_lease() -> None:
            nonlocal lease_lost
            while not work.done():
                await asyncio.sleep(self._renew_interval)
                try:
                    renewed = await self._renew(job)
                except Exception as exc:
                    # A transient database error must not abort parsing. If the lease lapses
                    # and another worker takes over, the next renewal reports the loss.
                    logger.warning(
                        "document_job_lease_renew_error job_id=%s error_type=%s",
                        job.public_id,
                        type(exc).__name__,
                    )
                    continue
                if not renewed:
                    lease_lost = True
                    work.cancel()
                    return

        keeper = asyncio.create_task(keep_lease())
        try:
            await work
        except asyncio.CancelledError:
            if not lease_lost:
                raise
            logger.warning(
                "document_job_lease_lost job_id=%s document_id=%s attempt=%s",
                job.public_id,
                job.document_public_id,
                job.attempt,
            )
        finally:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
        return True

    async def _renew(self, job: ClaimedJob) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            renewed = await unit_of_work.documents.renew_job_lease(
                job=job, lease_seconds=self._lease_seconds
            )
            await unit_of_work.commit()
        return renewed

    async def _process(self, job: ClaimedJob) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="review-agent-worker-",
            suffix=".pdf" if job.media_type == "application/pdf" else ".bin",
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
                completed = await unit_of_work.documents.complete_job(job=job, parsed=parsed)
                await unit_of_work.commit()
            if not completed:
                logger.warning(
                    "document_job_result_discarded job_id=%s document_id=%s attempt=%s",
                    job.public_id,
                    job.document_public_id,
                    job.attempt,
                )
                return
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

    async def _fail(self, *, job: ClaimedJob, code: str, message: str) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.documents.fail_job(
                job=job,
                failure_code=code,
                failure_message=message,
            )
            await unit_of_work.commit()
