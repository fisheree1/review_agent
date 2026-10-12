"""Planner intent cases keep deferred review and practice steps in the study plan."""

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import ModelSettings
from app.learning.graph_tasks import validate_graph_task_plan
from app.rag.providers import GRAPH_TASK_PROMPT, GRAPH_TASK_PROMPT_VERSION, CloudModels
from scripts import evaluate_study_plan
from scripts.evaluate_study_plan import EVAL_PATH, Case, Fixture, score_plan


def planned(case: Case, steps: list[str]) -> dict[str, Any]:
    writes_quiz = bool({"quiz", "practice"} & set(steps))
    return {
        "steps": steps,
        "summary_request": "Restated request" if "summary" in steps else None,
        "summary_mode": "focused",
        "title": "Practice" if writes_quiz else None,
        "config": (
            {
                "type_counts": case.type_counts
                or {"single": 5, "multiple": 0, "true_false": 0, "short": 0},
                "difficulty": "medium",
                "language": case.language or "en",
                "topic": "",
            }
            if writes_quiz
            else None
        ),
        "clarification": None,
        "memory_summary": "",
    }


def fixture() -> Fixture:
    return Fixture.model_validate_json(EVAL_PATH.read_text(encoding="utf-8"))


def test_fixture_covers_deferred_review_and_its_absence() -> None:
    categories = {case.category for case in fixture().cases}
    assert {"deferred_review", "deferred_practice", "no_review_requested"} <= categories


def test_plan_that_drops_the_requested_review_fails_the_case() -> None:
    # Regression: the live planner returned summary,quiz for an "after I submit, review" request,
    # so the run published the Quiz and completed instead of waiting to review the submission.
    case = next(c for c in fixture().cases if c.id == "explain-quiz-review-after-submit")
    dropped = validate_graph_task_plan(planned(case, ["summary", "quiz"]))
    assert not score_plan(case, dropped)["steps_match"]
    complete = validate_graph_task_plan(planned(case, ["summary", "quiz", "review"]))
    assert all(score_plan(case, complete).values())


def test_review_added_without_a_request_fails_the_case() -> None:
    case = next(c for c in fixture().cases if c.id == "quiz-only")
    extra = validate_graph_task_plan(planned(case, ["quiz", "review"]))
    assert not score_plan(case, extra)["steps_match"]


def test_every_expected_plan_is_executable() -> None:
    # An expectation the plan validator rejects could never pass, so it would hide regressions.
    for case in fixture().cases:
        plan = validate_graph_task_plan(planned(case, list(case.expected_steps)))
        assert all(score_plan(case, plan).values()), case.id


def test_fixture_rejects_unordered_expected_steps() -> None:
    with pytest.raises(ValueError):
        Case(id="x", category="x", request="review then quiz", expected_steps=["review", "quiz"])


def test_planner_sends_the_versioned_graph_prompt_and_reports_its_version() -> None:
    sent: list[dict[str, Any]] = []
    case = next(c for c in fixture().cases if c.id == "explain-quiz-review-after-submit")
    decision = planned(case, ["summary", "quiz", "review"])

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(decision)}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    async def run() -> tuple[dict[str, Any], dict[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            models = CloudModels(ModelSettings(deepseek_api_key=SecretStr("test-only")), client)
            return await models.plan_graph_task(
                case.request, history=[], review_available=False, memory_summary=""
            )

    payload, usage = asyncio.run(run())
    assert payload == decision
    assert usage["prompt_version"] == GRAPH_TASK_PROMPT_VERSION
    messages = sent[0]["messages"]
    assert messages[0] == {"role": "system", "content": GRAPH_TASK_PROMPT}
    context = json.loads(messages[1]["content"])
    assert context["current_request"] == case.request
    assert context["submitted_practice_in_current_scope"] is False


def test_live_report_is_content_free(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = fixture()

    class Model:
        def __init__(self, *_: object) -> None:
            pass

        async def plan_graph_task(
            self, request: str, **_: object
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            case = next(c for c in cases.cases if c.request == request)
            usage = {"prompt_version": GRAPH_TASK_PROMPT_VERSION}
            return planned(case, list(case.expected_steps)), usage

    monkeypatch.setattr(evaluate_study_plan, "CloudModels", Model)
    report = asyncio.run(evaluate_study_plan.evaluate(cases))
    assert all(row["passed"] for row in report["results"])
    serialized = json.dumps(report, ensure_ascii=False)
    assert not any(case.request in serialized for case in cases.cases)
