"""Recorded model responses replay through the real generation code without network access.

A failing case means a change altered outcomes, cited sources or the model-call sequence for
fixed inputs: re-check quality, then re-record deliberately rather than editing expectations.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from scripts.replay_agent_eval import (
    COMMITTED_DIR,
    LOCAL_DIR,
    ROOT,
    Case,
    cassette_paths,
    load,
    replay_case,
)

CASES = [case for path in cassette_paths(include_local=False) for case in load(path).cases]


def by_id(case_id: str) -> Case:
    return next(case for case in CASES if case.id == case_id)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_committed_cassette_replays_with_the_recorded_outcome(case: Case) -> None:
    _, problems = asyncio.run(replay_case(case))
    assert problems == []


def test_replay_flags_a_model_call_the_cassette_did_not_record() -> None:
    case = by_id("focused-repairs-paraphrased-quote-once")
    shortened = case.model_copy(update={"calls": case.calls[:1]})
    _, problems = asyncio.run(replay_case(shortened))
    assert problems and "unrecorded answer_study" in problems[0]


def test_replay_flags_recorded_calls_the_code_no_longer_makes() -> None:
    case = by_id("focused-cited-answer")
    padded = case.model_copy(update={"calls": [*case.calls, case.calls[0]]})
    _, problems = asyncio.run(replay_case(padded))
    assert "1 recorded calls were never made" in problems


def test_replay_flags_a_change_in_cited_sources() -> None:
    case = by_id("overview-batch-keeps-only-cited-points")
    expect = case.expect.model_copy(update={"cited_sources": ["median"]})
    _, problems = asyncio.run(replay_case(case.model_copy(update={"expect": expect})))
    assert any(problem.startswith("cited_sources") for problem in problems)


def test_live_recordings_are_written_only_to_an_ignored_directory() -> None:
    assert COMMITTED_DIR != LOCAL_DIR and LOCAL_DIR.is_relative_to(ROOT / "evals" / "local")
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(LOCAL_DIR / "probe.json")], cwd=ROOT, check=False
    )
    assert ignored.returncode == 0
