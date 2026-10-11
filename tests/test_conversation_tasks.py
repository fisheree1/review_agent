"""Task plans stay within the allowed actions, scope and Quiz limits."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.learning.conversation_tasks import validate_task_plan
from app.rag.domain import RagFailure

CONFIG = {
    "type_counts": {"single": 1, "multiple": 0, "true_false": 0, "short": 0},
    "difficulty": "medium",
    "language": "en",
    "topic": "statistics",
}


def test_chat_task_plan_accepts_chinese_english_quiz_language() -> None:
    plan = validate_task_plan(
        {"action": "create_quiz", "title": "双语练习", "config": {**CONFIG, "language": "zh-en"}}
    )
    assert plan.quiz_config()["language"] == "zh-en"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "delete", "document_id": str(uuid4())},
        {
            "action": "create_quiz",
            "title": "Practice",
            "config": {**CONFIG, "type_counts": {"single": 11}},
        },
        {"action": "review_mistakes", "attempt_id": str(uuid4())},
    ],
)
def test_task_plans_cannot_expand_tools_scope_or_question_limits(payload: dict[str, Any]) -> None:
    with pytest.raises(RagFailure) as caught:
        validate_task_plan(payload)
    assert caught.value.code == "TASK_PLAN_INVALID"


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
