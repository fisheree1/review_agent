from __future__ import annotations

import asyncio
import logging
import os
import signal

import httpx

from app.core.config import ModelSettings, RagSettings, WorkerSettings
from app.core.database import async_session_factory, close_database, verify_runtime_database_role
from app.documents.application.worker import DocumentJobProcessor
from app.documents.infrastructure.pdf_parser import PypdfDocumentParser
from app.documents.infrastructure.repository import SqlAlchemyDocumentsUnitOfWork
from app.documents.infrastructure.storage import MinioDocumentStorage
from app.jobs.heartbeat import run_worker_heartbeat
from app.jobs.lanes import (
    AGENT,
    INGEST,
    INTERACTIVE,
    Lane,
    NamedStep,
    parse_lane_names,
    run_lanes,
    throttled,
)
from app.learning.application import LearningProcessor
from app.learning.focused_answer import reader_answerer
from app.learning.run_application import AgentRunProcessor
from app.learning.run_store import SqlAgentRunStore
from app.learning.store import SqlLearningStore
from app.rag.application import RagProcessor
from app.rag.providers import CloudModels
from app.rag.store import SqlRagStore

# Expired runs and lapsed leases are found by table scans, so they run on a fixed interval.
RUN_SWEEP_SECONDS = 30.0


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
        parser=PypdfDocumentParser(
            timeout_seconds=settings.pdf_parser_timeout_seconds,
            max_pages=settings.max_pdf_pages,
            max_characters=settings.max_pdf_characters,
        ),
        lease_seconds=settings.job_lease_seconds,
    )

    model_client = httpx.AsyncClient(follow_redirects=False)
    models = CloudModels(ModelSettings(), model_client)
    rag = RagProcessor(
        SqlRagStore(async_session_factory, RagSettings().profile),
        models,
        reader_answerer(models, models),
    )
    learning = LearningProcessor(
        SqlLearningStore(async_session_factory, RagSettings().profile), models, models
    )
    run_store = SqlAgentRunStore(learning.store)
    runs = AgentRunProcessor(run_store, models, models)
    available = {
        # Short, user-facing model work: a person is waiting on each of these.
        INTERACTIVE: Lane(
            INTERACTIVE,
            (
                NamedStep("rag_question", rag.process_question),
                NamedStep("quiz_grading", learning.process_grading),
            ),
            settings.worker_interactive_concurrency,
        ),
        # Multi-step agent runs and Quiz generation may hold a slot for several minutes.
        # Disabling new graph requests must not strand existing waiting runs.
        AGENT: Lane(
            AGENT,
            (
                # Expiry and lapsed-lease recovery scan the table, so they run periodically.
                NamedStep("run_sweep", throttled(run_store.sweep, seconds=RUN_SWEEP_SECONDS)),
                NamedStep("agent_run", runs.process_next),
                NamedStep("quiz_generation", learning.process_quiz),
            ),
            settings.worker_agent_concurrency,
        ),
        # PDF parsing/OCR and embedding batches: CPU, memory and bulk provider usage.
        INGEST: Lane(
            INGEST,
            (
                NamedStep("document_parse", processor.process_next),
                NamedStep("rag_index", rag.process_index),
            ),
            settings.worker_ingest_concurrency,
        ),
    }
    lanes = [available[name] for name in parse_lane_names(settings.worker_lanes)]
    logger.info(
        "worker_lanes_configured lanes=%s",
        ",".join(f"{lane.name}x{lane.concurrency}" for lane in lanes),
    )
    try:
        await run_lanes(lanes, stop_event=stop_event, poll_seconds=settings.job_poll_seconds)
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
