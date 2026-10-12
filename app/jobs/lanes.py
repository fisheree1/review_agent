"""Independent polling lanes so slow work cannot block interactive work.

Every job type is claimed from PostgreSQL with ``FOR UPDATE SKIP LOCKED`` and a fenced lease,
so any number of lane loops (in one process or many) may poll the same tables safely.
Within a lane, steps run in order to keep the original fairness between job types; across
lanes, loops run concurrently.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

Step = Callable[[], Awaitable[bool]]

INTERACTIVE = "interactive"
AGENT = "agent"
INGEST = "ingest"
LANE_NAMES = (INTERACTIVE, AGENT, INGEST)

# After an unexpected step error, wait before polling again so a broken dependency does not
# turn into a hot loop of failing queries.
ERROR_BACKOFF_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class NamedStep:
    name: str
    run: Step


@dataclass(frozen=True, slots=True)
class Lane:
    name: str
    steps: tuple[NamedStep, ...]
    concurrency: int = 1


def parse_lane_names(value: str) -> tuple[str, ...]:
    names = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    unknown = [name for name in names if name not in LANE_NAMES]
    if not names or unknown:
        raise ValueError(f"WORKER_LANES must list lanes from {', '.join(LANE_NAMES)}")
    return names


async def _run_once(lane: Lane, slot: int) -> tuple[bool, bool]:
    """Run each step once; return (processed_any, had_error)."""
    processed = False
    failed = False
    for step in lane.steps:
        try:
            processed = await step.run() or processed
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Processors already map expected failures onto job state. Anything reaching here
            # is unexpected (for example a database outage); keep the other lanes alive.
            failed = True
            logger.error(
                "worker_step_failed lane=%s slot=%s step=%s error_type=%s",
                lane.name,
                slot,
                step.name,
                type(exc).__name__,
            )
    return processed, failed


async def run_lane(
    lane: Lane,
    *,
    slot: int,
    stop_event: asyncio.Event,
    poll_seconds: float,
    error_backoff_seconds: float = ERROR_BACKOFF_SECONDS,
) -> None:
    logger.info("worker_lane_started lane=%s slot=%s", lane.name, slot)
    while not stop_event.is_set():
        processed, failed = await _run_once(lane, slot)
        if processed and not failed:
            continue
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=error_backoff_seconds if failed else poll_seconds
            )
        except TimeoutError:
            pass
    logger.info("worker_lane_stopped lane=%s slot=%s", lane.name, slot)


async def run_lanes(
    lanes: Iterable[Lane],
    *,
    stop_event: asyncio.Event,
    poll_seconds: float,
    error_backoff_seconds: float = ERROR_BACKOFF_SECONDS,
) -> None:
    async with asyncio.TaskGroup() as group:
        for lane in lanes:
            for slot in range(lane.concurrency):
                group.create_task(
                    run_lane(
                        lane,
                        slot=slot,
                        stop_event=stop_event,
                        poll_seconds=poll_seconds,
                        error_backoff_seconds=error_backoff_seconds,
                    ),
                    name=f"worker-lane-{lane.name}-{slot}",
                )
