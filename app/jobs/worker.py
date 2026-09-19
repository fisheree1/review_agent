from __future__ import annotations

import asyncio
import signal

from app.core.config import WorkerSettings
from app.core.database import async_session_factory, close_database, verify_runtime_database_role
from app.documents.application.worker import DocumentJobProcessor
from app.documents.infrastructure.pdf_parser import PypdfDocumentParser
from app.documents.infrastructure.repository import SqlAlchemyDocumentsUnitOfWork
from app.documents.infrastructure.storage import MinioDocumentStorage


async def run_worker() -> None:
    settings = WorkerSettings()  # type: ignore[call-arg]
    await verify_runtime_database_role()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop_event.set)

    storage = MinioDocumentStorage(
        endpoint=settings.storage_endpoint,
        access_key=settings.storage_access_key,
        secret_key=settings.storage_secret_key.get_secret_value(),
        bucket=settings.storage_bucket,
        secure=settings.storage_secure,
    )
    processor = DocumentJobProcessor(
        unit_of_work_factory=lambda: SqlAlchemyDocumentsUnitOfWork(async_session_factory),
        storage=storage,
        parser=PypdfDocumentParser(
            timeout_seconds=settings.pdf_parser_timeout_seconds,
            max_pages=settings.max_pdf_pages,
            max_characters=settings.max_pdf_characters,
        ),
        lease_seconds=settings.job_lease_seconds,
    )

    try:
        while not stop_event.is_set():
            processed = await processor.process_next()
            if not processed:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=settings.job_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        await close_database()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
