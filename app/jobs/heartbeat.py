from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.jobs.models import WorkerHeartbeatModel

logger = logging.getLogger(__name__)


async def run_worker_heartbeat(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    worker_id: str,
    worker_type: str,
    interval_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    started_at = datetime.now(UTC)
    while not stop_event.is_set():
        now = datetime.now(UTC)
        try:
            async with session_factory() as session:
                statement = insert(WorkerHeartbeatModel).values(
                    worker_id=worker_id,
                    worker_type=worker_type,
                    started_at=started_at,
                    last_seen_at=now,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[WorkerHeartbeatModel.worker_id],
                    set_={"worker_type": worker_type, "last_seen_at": now},
                )
                await session.execute(statement)
                await session.commit()
        except Exception as exc:
            logger.error(
                "worker_heartbeat_failed worker_id=%s worker_type=%s error_type=%s",
                worker_id,
                worker_type,
                type(exc).__name__,
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
