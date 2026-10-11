"""Fan PostgreSQL run notifications out to progress streams in this API process.

Migration 0020 makes ``agent_runs`` changes ``pg_notify`` the run's public UUID on commit. One
dedicated listener connection per process wakes the subscribers of that run; subscribers then
re-read the run through the authorized store, so notifications never carry content. Wake-ups
coalesce: a subscriber that is already signalled is not queued twice. If the listener
connection drops, every subscriber is told to stop and clients fall back to polling.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import cache
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

CHANNEL = "agent_run_events"
MAX_SUBSCRIBERS = 200

Connect = Callable[[str], Awaitable[Any]]


class StreamCapacityExceeded(Exception):
    """This process already serves its maximum number of progress streams."""


def _signal(queue: asyncio.Queue[bool], alive: bool) -> None:
    if not alive:
        # A stop signal must win over a pending wake-up.
        while not queue.empty():
            queue.get_nowait()
    if queue.empty():
        queue.put_nowait(alive)


class RunEventHub:
    def __init__(
        self,
        dsn: str,
        *,
        max_subscribers: int = MAX_SUBSCRIBERS,
        connect: Connect = asyncpg.connect,
    ) -> None:
        self._dsn = dsn
        self._max_subscribers = max_subscribers
        self._connect = connect
        self._connection: Any = None
        self._lock = asyncio.Lock()
        self._subscribers: dict[UUID, set[asyncio.Queue[bool]]] = {}

    @property
    def subscriber_count(self) -> int:
        return sum(len(queues) for queues in self._subscribers.values())

    async def _ensure_listening(self) -> None:
        async with self._lock:
            if self._connection is not None and not self._connection.is_closed():
                return
            connection = await self._connect(self._dsn)
            await connection.add_listener(CHANNEL, self._on_notify)
            connection.add_termination_listener(self._on_terminate)
            self._connection = connection
            logger.info("run_event_listener_started")

    def _on_notify(self, _connection: Any, _pid: int, _channel: str, payload: str) -> None:
        try:
            run_id = UUID(payload)
        except ValueError:
            return
        for queue in self._subscribers.get(run_id, ()):
            _signal(queue, True)

    def _on_terminate(self, _connection: Any) -> None:
        logger.warning("run_event_listener_lost subscribers=%s", self.subscriber_count)
        self._connection = None
        for queues in self._subscribers.values():
            for queue in queues:
                _signal(queue, False)

    @asynccontextmanager
    async def subscribe(self, run_id: UUID) -> AsyncIterator[asyncio.Queue[bool]]:
        """Yield a queue that receives True on change and False when the listener is lost."""
        if self.subscriber_count >= self._max_subscribers:
            raise StreamCapacityExceeded
        await self._ensure_listening()
        queue: asyncio.Queue[bool] = asyncio.Queue(maxsize=1)
        self._subscribers.setdefault(run_id, set()).add(queue)
        try:
            yield queue
        finally:
            queues = self._subscribers.get(run_id)
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    del self._subscribers[run_id]

    async def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None and not connection.is_closed():
            await connection.close()


@cache
def get_run_event_hub() -> RunEventHub:
    from app.core.database import engine

    dsn = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    return RunEventHub(dsn)


async def close_run_event_hub() -> None:
    if get_run_event_hub.cache_info().currsize:
        await get_run_event_hub().close()
        get_run_event_hub.cache_clear()
