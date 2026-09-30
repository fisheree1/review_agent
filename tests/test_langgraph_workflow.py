from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.learning.agent import AgentBudget, StudyPlanningModel
from app.learning.conversation_tasks import validate_task_plan
from app.learning.infrastructure.langgraph_workflow import LangGraphStudyExecutor
from app.learning.quiz_agent import QuizPlanningModel
from app.rag.domain import STUDY_PROMPT_VERSION, Evidence, RagFailure
from app.rag.ports import Embeddings

USAGE = {
    "model": "fake",
    "prompt_version": "test-planner-v1",
    "prompt_tokens": 20,
    "completion_tokens": 5,
}
CONFIG = {
    "type_counts": {"single": 1, "multiple": 0, "true_false": 0, "short": 0},
    "difficulty": "medium",
    "language": "en",
    "topic": "median",
}


def setup() -> tuple[Evidence, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    source = Evidence(
        uuid4(),
        "The median resists extreme outliers.",
        1,
        {"kind": "page", "position": 1},
        document_id=uuid4(),
        version_id=7,
    )
    model = AsyncMock(spec=StudyPlanningModel)
    model.plan_step.side_effect = [
        ({"tool": "search", "query": "median"}, USAGE),
        ({"tool": "read_source", "source_id": str(source.id)}, USAGE),
        ({"tool": "answer"}, USAGE),
    ]
    model.answer_study.return_value = (
        {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "Median resists outliers.",
                    "citations": [{"source_id": str(source.id), "quote": source.content}],
                }
            ],
            "explanation": "An extreme value changes the mean more than the median.",
        },
        {**USAGE, "prompt_version": STUDY_PROMPT_VERSION},
    )
    embeddings = AsyncMock(spec=Embeddings)
    embeddings.embed.return_value = [[1.0] * 1024]
    search = AsyncMock(return_value=[source])
    return source, model, embeddings, search, AsyncMock()


def execute(
    model: Any, embeddings: Any, search: Any, active: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    return asyncio.run(
        LangGraphStudyExecutor().answer(
            "median",
            [],
            model=model,
            embeddings=embeddings,
            search=search,
            ensure_active=active,
            budget=AgentBudget(),
        )
    )


def test_graph_publishes_only_validated_scoped_citations() -> None:
    source, model, embeddings, search, active = setup()
    answer, usage = execute(model, embeddings, search, active)
    assert answer["claims"][0]["citations"][0]["document_id"] == str(source.document_id)
    assert answer["explanation"] == "An extreme value changes the mean more than the median."
    assert usage["model_calls"] == 4 and usage["graph_version"] == "study-graph-v2"
    assert usage["planning_prompt_version"] == "test-planner-v1"
    assert model.answer_study.call_args.args[1] == [source]


def test_graph_refuses_without_evidence_and_does_not_generate() -> None:
    _, model, embeddings, search, active = setup()
    search.return_value = []
    model.plan_step.side_effect = [
        ({"tool": "search", "query": "missing"}, USAGE),
        ({"tool": "answer"}, USAGE),
    ]
    answer, _ = execute(model, embeddings, search, active)
    assert answer == {"insufficient_evidence": True, "claims": []}
    model.answer_study.assert_not_awaited()


@pytest.mark.parametrize(
    "invalid",
    [
        {"tool": "read_source", "source_id": str(uuid4())},
        {"tool": "search", "query": "median"},
        {"tool": "generate_quiz"},
    ],
)
def test_graph_rejects_foreign_source_duplicate_search_and_wrong_tool(
    invalid: dict[str, Any],
) -> None:
    _, model, embeddings, search, active = setup()
    model.plan_step.side_effect = [({"tool": "search", "query": "median"}, USAGE), (invalid, USAGE)]
    with pytest.raises(RagFailure):
        execute(model, embeddings, search, active)
    model.answer_study.assert_not_awaited()


def test_graph_cancel_before_generation_never_calls_answer() -> None:
    _, model, embeddings, search, active = setup()
    active.side_effect = RagFailure("AGENT_RUN_INACTIVE", "cancelled")
    with pytest.raises(RagFailure, match="cancelled"):
        execute(model, embeddings, search, active)
    model.plan_step.assert_not_awaited()


def test_graph_quiz_rejects_missing_answer_and_source_before_publication() -> None:
    source, _, embeddings, search, active = setup()
    model = AsyncMock(spec=QuizPlanningModel)
    model.plan_quiz_step.side_effect = [
        ({"tool": "search", "query": "median"}, USAGE),
        ({"tool": "read_source", "source_id": str(source.id)}, USAGE),
        ({"tool": "generate_quiz"}, USAGE),
    ]
    model.generate_quiz_with_usage.return_value = (
        {"questions": [{"kind": "single", "stem": "Unsupported?"}]},
        USAGE,
    )
    with pytest.raises(RagFailure) as exc:
        asyncio.run(
            LangGraphStudyExecutor().quiz(
                CONFIG,
                embeddings=embeddings,
                model=model,
                search=search,
                ensure_active=active,
                budget=AgentBudget(),
            )
        )
    assert exc.value.code == "QUIZ_INVALID"


def test_study_plan_cannot_add_weak_practice_without_review_or_alter_single_task_steps() -> None:
    with pytest.raises(RagFailure):
        validate_task_plan(
            {
                "action": "study",
                "title": "Study",
                "config": CONFIG,
                "summary_request": "Summarize median",
                "practice_after_review": True,
            }
        )
    with pytest.raises(RagFailure):
        validate_task_plan({"action": "answer", "review_after_submit": True})


def test_planner_remembers_empty_search_and_refuses_instead_of_repeating_it() -> None:
    _, model, embeddings, search, active = setup()
    search.return_value = []

    async def plan(question: str, *, history: list, observations: list):
        already_searched = any(o.get("query") == "missing" for o in observations)
        return (
            {"tool": "answer"} if already_searched else {"tool": "search", "query": "missing"}
        ), USAGE

    model.plan_step.side_effect = plan
    answer, _ = execute(model, embeddings, search, active)
    assert answer["insufficient_evidence"]
    search.assert_awaited_once()
    model.answer_study.assert_not_awaited()
