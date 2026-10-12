from __future__ import annotations

import asyncio
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4

import pytest

from app.documents.application.errors import DocumentProcessingError
from app.documents.application.worker import DocumentJobProcessor
from app.documents.domain.entities import (
    CitationLocator,
    CitationLocatorKind,
    ClaimedJob,
    DocumentContent,
    ParsedDocument,
)

PARSED = ParsedDocument(
    contents=(DocumentContent(1, "正文", CitationLocator(CitationLocatorKind.PAGE, 1)),),
    parser_name="fake",
    parser_version="1",
)


def claimed(attempt: int = 1) -> ClaimedJob:
    return ClaimedJob(
        id=1,
        public_id=uuid4(),
        workspace_id=1,
        document_id=1,
        document_public_id=uuid4(),
        object_key="objects/a.pdf",
        media_type="application/pdf",
        source_sha256="0" * 64,
        attempt=attempt,
    )


class FakeRepository:
    def __init__(self, *, renew_results: list[bool] | None = None, complete: bool = True) -> None:
        self.job: ClaimedJob | None = claimed()
        self.renew_results = renew_results
        self.renewals = 0
        self.complete_result = complete
        self.completed: list[ClaimedJob] = []
        self.failed: list[tuple[ClaimedJob, str]] = []

    async def claim_next_job(self, *, lease_seconds: int) -> ClaimedJob | None:
        job, self.job = self.job, None
        return job

    async def renew_job_lease(self, *, job: ClaimedJob, lease_seconds: int) -> bool:
        self.renewals += 1
        if self.renew_results:
            return self.renew_results.pop(0)
        return True

    async def complete_job(self, *, job: ClaimedJob, parsed: ParsedDocument) -> bool:
        self.completed.append(job)
        return self.complete_result

    async def fail_job(self, *, job: ClaimedJob, failure_code: str, failure_message: str) -> bool:
        self.failed.append((job, failure_code))
        return True


class FakeUnitOfWork:
    def __init__(self, repository: FakeRepository) -> None:
        self.documents: Any = repository

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None


class FakeStorage:
    async def upload(self, *, object_key: str, source_path: Path, length: int) -> None:
        raise AssertionError("not used")

    async def download(self, *, object_key: str, destination_path: Path) -> None:
        await asyncio.to_thread(destination_path.write_bytes, b"%PDF-1.7")

    async def delete(self, *, object_key: str) -> None:
        raise AssertionError("not used")


class SlowParser:
    def __init__(self, seconds: float, error: Exception | None = None) -> None:
        self.seconds = seconds
        self.error = error
        self.cancelled = False
        self.paths: list[Path] = []

    async def parse(self, source_path: Path, *, media_type: str) -> ParsedDocument:
        self.paths.append(source_path)
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error is not None:
            raise self.error
        return PARSED


def processor(repository: FakeRepository, parser: SlowParser) -> DocumentJobProcessor:
    return DocumentJobProcessor(
        unit_of_work_factory=lambda: FakeUnitOfWork(repository),
        storage=FakeStorage(),
        parser=parser,
        lease_seconds=1,
        renew_interval_seconds=0.02,
    )


def test_long_parse_keeps_renewing_lease_and_completes() -> None:
    async def check() -> None:
        repository = FakeRepository()
        parser = SlowParser(0.15)

        assert await processor(repository, parser).process_next() is True

        assert repository.renewals >= 3
        assert len(repository.completed) == 1
        assert repository.failed == []
        assert not parser.paths[0].exists()

    asyncio.run(check())


def test_lost_lease_cancels_parse_without_writing_results() -> None:
    async def check() -> None:
        repository = FakeRepository(renew_results=[True, False])
        parser = SlowParser(5)

        assert await asyncio.wait_for(processor(repository, parser).process_next(), 2) is True

        assert parser.cancelled is True
        assert repository.completed == []
        assert repository.failed == []
        assert not parser.paths[0].exists()

    asyncio.run(check())


def test_stale_completion_is_discarded_without_failing_job() -> None:
    async def check() -> None:
        repository = FakeRepository(complete=False)

        assert await processor(repository, SlowParser(0)).process_next() is True

        assert len(repository.completed) == 1
        assert repository.failed == []

    asyncio.run(check())


def test_parser_failure_is_recorded_with_its_code() -> None:
    async def check() -> None:
        repository = FakeRepository()
        parser = SlowParser(0, DocumentProcessingError(code="PDF_TEXT_NOT_FOUND", message="x"))

        assert await processor(repository, parser).process_next() is True

        assert [code for _, code in repository.failed] == ["PDF_TEXT_NOT_FOUND"]

    asyncio.run(check())


def test_no_job_returns_false() -> None:
    async def check() -> None:
        repository = FakeRepository()
        repository.job = None

        assert await processor(repository, SlowParser(0)).process_next() is False

    asyncio.run(check())


def test_outer_cancellation_still_propagates() -> None:
    async def check() -> None:
        repository = FakeRepository()
        parser = SlowParser(5)
        task = asyncio.create_task(processor(repository, parser).process_next())
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert parser.cancelled is True

    asyncio.run(check())
