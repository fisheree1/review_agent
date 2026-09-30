"""A single request can ask for several bounded, source-grounded outcomes."""

import pytest

from app.learning.graph_tasks import validate_graph_task_plan
from app.rag.domain import RagFailure

QUIZ = {
    "type_counts": {"single": 5, "multiple": 0, "true_false": 0, "short": 0},
    "difficulty": "medium",
    "language": "zh-en",
    "topic": "",
}


def test_notes_pdf_and_bilingual_quiz_can_share_one_request() -> None:
    plan = validate_graph_task_plan(
        {
            "steps": ["summary", "pdf", "quiz", "review", "practice"],
            "summary_request": "整理主要知识点和出处",
            "summary_mode": "overview",
            "title": "双语自测",
            "config": QUIZ,
            "memory_summary": "用户偏好每题中英对照",
        }
    )
    assert plan.steps == ["summary", "pdf", "quiz", "review", "practice"]
    assert plan.quiz_config()["language"] == "zh-en"


@pytest.mark.parametrize(
    "payload",
    [
        {"steps": ["pdf"]},
        {"steps": ["practice"], "title": "练习", "config": QUIZ},
        {"steps": ["quiz", "summary"], "title": "练习", "config": QUIZ},
        {"steps": ["summary", "quiz"], "summary_request": "总结资料"},
        {"steps": ["summary"], "clarification": "请说明资料"},
        {"steps": ["summary"], "memory_summary": "x" * 701},
        {"steps": ["search_web"]},
    ],
)
def test_invalid_plan_cannot_skip_prerequisites_or_expand_tools(payload: dict) -> None:
    with pytest.raises(RagFailure) as exc:
        validate_graph_task_plan(payload)
    assert exc.value.code == "TASK_PLAN_INVALID"
