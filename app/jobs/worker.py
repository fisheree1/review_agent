from __future__ import annotations

import asyncio
import logging
import os
import signal

import httpx

from app.core.config import ModelSettings, RagSettings, WorkerSettings
from app.core.database import async_session_factory, close_database, verify_runtime_database_role
from app.documents.application.worker import DocumentJobProcessor
from app.documents.infrastructure.document_parser import DocumentParserRegistry
from app.documents.infrastructure.office_parser import OoxmlDocumentParser
from app.documents.infrastructure.pdf_parser import PypdfDocumentParser
from app.documents.infrastructure.repository import SqlAlchemyDocumentsUnitOfWork
from app.documents.infrastructure.storage import MinioDocumentStorage
from app.jobs.heartbeat import run_worker_heartbeat
from app.learning.application import LearningProcessor
from app.learning.store import SqlLearningStore
from app.rag.application import RagProcessor
from app.rag.providers import CloudModels
from app.rag.store import SqlRagStore


async def run_worker() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger(__name__)
    settings = WorkerSettings()  # type: ignore[call-arg]
    await verify_runtime_database_role()
    logger.info("document_worker_started")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop_event.set)
    worker_id = os.environ.get("HOSTNAME", f"document-worker-{os.getpid()}")
    heartbeat_task = asyncio.create_task(
        run_worker_heartbeat(
            session_factory=async_session_factory,
            worker_id=worker_id,
            worker_type="document-parser",
            interval_seconds=settings.worker_heartbeat_seconds,
            stop_event=stop_event,
        )
    )

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
        parser=DocumentParserRegistry(
            pdf_parser=PypdfDocumentParser(
                timeout_seconds=settings.pdf_parser_timeout_seconds,
                max_pages=settings.max_pdf_pages,
                max_characters=settings.max_pdf_characters,
            ),
            office_parser=OoxmlDocumentParser(
                timeout_seconds=settings.office_parser_timeout_seconds,
                max_units=settings.max_office_units,
                max_characters=settings.max_office_characters,
                max_uncompressed_bytes=settings.max_office_uncompressed_bytes,
            ),
        ),
        lease_seconds=settings.job_lease_seconds,
    )

    model_client = httpx.AsyncClient(follow_redirects=False)
    models = CloudModels(ModelSettings(), model_client)
    rag = RagProcessor(SqlRagStore(async_session_factory, RagSettings().profile), models, models)
    learning = LearningProcessor(
        SqlLearningStore(async_session_factory, RagSettings().profile), models, models
    )
    try:
        while not stop_event.is_set():
            processed = await processor.process_next()
            processed = await rag.process_question() or processed
            processed = await learning.process_message() or processed
            processed = await learning.process_grading() or processed
            processed = await learning.process_quiz() or processed
            processed = await rag.process_index() or processed
            if not processed:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=settings.job_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        await model_client.aclose()
        stop_event.set()
        await heartbeat_task
        logger.info("document_worker_stopped")
        await close_database()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
