from __future__ import annotations

import asyncio

import pytest

from app.jobs.lanes import Lane, NamedStep, parse_lane_names, run_lanes


def test_lane_names_are_validated_and_deduplicated() -> None:
    assert parse_lane_names(" ingest, interactive ,ingest") == ("ingest", "interactive")
    for invalid in ("", " , ", "interactive,batch"):
        with pytest.raises(ValueError):
            parse_lane_names(invalid)


def test_slow_ingest_work_does_not_block_interactive_lane() -> None:
    async def check() -> None:
        stop = asyncio.Event()
        answered: list[float] = []
        slow_started = asyncio.Event()
        questions = [1, 2, 3]

        async def slow_parse() -> bool:
            slow_started.set()
            await stop.wait()
            return True

        async def answer_question() -> bool:
            await slow_started.wait()
            if not questions:
                return False
            questions.pop()
            answered.append(asyncio.get_running_loop().time())
            if not questions:
                stop.set()
            return True

        lanes = [
            Lane("ingest", (NamedStep("parse", slow_parse),)),
            Lane("interactive", (NamedStep("question", answer_question),)),
        ]
        await asyncio.wait_for(run_lanes(lanes, stop_event=stop, poll_seconds=0.01), 2)
        assert len(answered) == 3

    asyncio.run(check())


def test_step_error_is_isolated_and_lane_keeps_polling() -> None:
    async def check() -> None:
        stop = asyncio.Event()
        calls = {"broken": 0, "healthy": 0}

        async def broken() -> bool:
            calls["broken"] += 1
            raise RuntimeError("database unavailable")

        async def healthy() -> bool:
            calls["healthy"] += 1
            if calls["healthy"] >= 3:
                stop.set()
            return False

        lane = Lane("interactive", (NamedStep("broken", broken), NamedStep("ok", healthy)))
        await asyncio.wait_for(
            run_lanes([lane], stop_event=stop, poll_seconds=0.01, error_backoff_seconds=0.01), 2
        )
        assert calls["broken"] >= 3
        assert calls["healthy"] >= 3

    asyncio.run(check())


def test_concurrency_runs_parallel_slots_of_one_lane() -> None:
    async def check() -> None:
        stop = asyncio.Event()
        running = 0
        peak = 0

        async def step() -> bool:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1
            stop.set()
            return True

        lane = Lane("interactive", (NamedStep("question", step),), concurrency=2)
        await asyncio.wait_for(run_lanes([lane], stop_event=stop, poll_seconds=0.01), 2)
        assert peak == 2

    asyncio.run(check())
