"""Run progress streams push committed changes without leaking content or other runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.auth import Principal, get_current_principal
from app.core.errors import ApplicationError
from app.learning import api as learning_api
from app.learning.api import run_event_stream
from app.learning.run_events import CHANNEL, RunEventHub, StreamCapacityExceeded
from app.main import app


def view(run_id: UUID, status: str = "running", revision: int = 1) -> dict[str, Any]:
    return {
        "id": run_id,
        "status": status,
        "stage": "summary",
        "revision": revision,
        "scope": [],
        "graph_version": "study-graph-v5",
        "plan": None,
        "outputs": [],
        "clarification": None,
        "failure_code": None,
        "failure_message": None,
        "expires_at": datetime.now(UTC) + timedelta(days=1),
    }


async def never_disconnected() -> bool:
    return False


async def collect(stream: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in stream]


def events(chunks: list[str]) -> list[str]:
    return [chunk for chunk in chunks if chunk.startswith("event: run")]


def test_stream_sends_snapshot_then_each_committed_change_until_the_run_closes() -> None:
    run_id = uuid4()
    changes: asyncio.Queue[bool] = asyncio.Queue(maxsize=1)
    reads = iter([view(run_id, "running", 2), view(run_id, "completed", 3)])

    async def read_run() -> dict[str, Any]:
        if changes.empty():
            changes.put_nowait(True)
        return next(reads)

    changes.put_nowait(True)
    chunks = asyncio.run(
        collect(run_event_stream(read_run, view(run_id), changes, never_disconnected))
    )
    assert chunks[0].startswith("retry:")
    assert [
        f'"revision":{n}' in chunk for n, chunk in zip((1, 2, 3), events(chunks), strict=True)
    ] == [True] * 3
    assert '"status":"completed"' in events(chunks)[-1]


def test_stream_sends_heartbeats_and_stops_at_its_time_limit() -> None:
    run_id = uuid4()

    async def read_run() -> dict[str, Any]:
        raise AssertionError("no change was signalled")

    chunks = asyncio.run(
        collect(
            run_event_stream(
                read_run,
                view(run_id, "waiting_input"),
                asyncio.Queue(maxsize=1),
                never_disconnected,
                heartbeat=0.01,
                max_seconds=0.05,
            )
        )
    )
    assert len(events(chunks)) == 1 and ": keep-alive\n\n" in chunks


@pytest.mark.parametrize("reason", ["listener_lost", "deleted", "disconnected"])
def test_stream_ends_when_it_can_no_longer_be_trusted(reason: str) -> None:
    run_id = uuid4()
    changes: asyncio.Queue[bool] = asyncio.Queue(maxsize=1)
    changes.put_nowait(reason != "listener_lost")

    async def read_run() -> dict[str, Any]:
        raise ApplicationError(code="RESOURCE_NOT_FOUND", message="gone", status_code=404)

    async def disconnected() -> bool:
        return reason == "disconnected"

    chunks = asyncio.run(collect(run_event_stream(read_run, view(run_id), changes, disconnected)))
    assert len(events(chunks)) == 1


class FakeConnection:
    def __init__(self) -> None:
        self.listener: Any = None
        self.on_terminate: Any = None
        self.closed = False

    async def add_listener(self, channel: str, callback: Any) -> None:
        assert channel == CHANNEL
        self.listener = callback

    def add_termination_listener(self, callback: Any) -> None:
        self.on_terminate = callback

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


def hub_with_connection(max_subscribers: int = 10) -> tuple[RunEventHub, FakeConnection, list[str]]:
    connection = FakeConnection()
    opened: list[str] = []

    async def connect(dsn: str) -> FakeConnection:
        opened.append(dsn)
        return connection

    return (
        RunEventHub("postgresql://test", max_subscribers=max_subscribers, connect=connect),
        connection,
        opened,
    )


def test_hub_wakes_only_subscribers_of_the_notified_run_and_coalesces() -> None:
    async def check() -> None:
        hub, connection, opened = hub_with_connection()
        mine, other = uuid4(), uuid4()
        async with hub.subscribe(mine) as queue, hub.subscribe(other) as other_queue:
            connection.listener(connection, 1, CHANNEL, str(mine))
            connection.listener(connection, 1, CHANNEL, str(mine))
            connection.listener(connection, 1, CHANNEL, "not-a-uuid")
            assert queue.qsize() == 1 and queue.get_nowait() is True
            assert other_queue.empty()
        assert hub.subscriber_count == 0 and opened == ["postgresql://test"]

    asyncio.run(check())


def test_lost_listener_stops_every_stream_and_reconnects_for_new_ones() -> None:
    async def check() -> None:
        hub, connection, opened = hub_with_connection()
        async with hub.subscribe(uuid4()) as queue:
            connection.listener(connection, 1, CHANNEL, str(uuid4()))
            connection.closed = True
            connection.on_terminate(connection)
            assert queue.get_nowait() is False
        connection.closed = False
        async with hub.subscribe(uuid4()):
            assert len(opened) == 2

    asyncio.run(check())


def test_hub_refuses_streams_beyond_its_capacity() -> None:
    async def check() -> None:
        hub, _, _ = hub_with_connection(max_subscribers=1)
        async with hub.subscribe(uuid4()):
            with pytest.raises(StreamCapacityExceeded):
                async with hub.subscribe(uuid4()):
                    pass

    asyncio.run(check())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_foreign_run_is_rejected_before_any_stream_or_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub, _, _ = hub_with_connection()

    class ForeignRuns:
        def __init__(self, _store: Any) -> None:
            pass

        async def get(self, workspace: UUID, run_id: UUID) -> dict[str, Any]:
            raise ApplicationError(
                code="RESOURCE_NOT_FOUND", message="资料或学习记录不存在", status_code=404
            )

    monkeypatch.setattr(learning_api, "SqlAgentRunStore", ForeignRuns)
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_public_id=uuid4(), workspace_public_id=uuid4()
    )
    app.dependency_overrides[learning_api.get_learning_service] = lambda: SimpleNamespace(
        store=None
    )
    app.dependency_overrides[learning_api.get_run_event_hub] = lambda: hub
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/agent-runs/{uuid4()}/events")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert hub.subscriber_count == 0
