from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.learning.application import LearningModel, LearningProcessor
from app.learning.conversation_tasks import validate_task_plan
from app.learning.store import SqlLearningStore
from app.rag.domain import Evidence, RagFailure
from app.rag.ports import Embeddings

USAGE = {"model": "fake", "prompt_tokens": 20, "completion_tokens": 5}
CONFIG = {
    "type_counts": {"single": 1, "multiple": 0, "true_false": 0, "short": 0},
    "difficulty": "medium",
    "language": "en",
    "topic": "statistics",
}


def processor(
    plan: dict[str, Any], review: dict[str, Any] | None = None
) -> tuple[LearningProcessor, AsyncMock, AsyncMock, AsyncMock]:
    store = AsyncMock(spec=SqlLearningStore)
    store.claim_message.return_value = (uuid4(), uuid4(), 1, "A user task", [], [])
    store.message_review_context.return_value = review
    model = AsyncMock(spec=LearningModel)
    model.plan_task.return_value = (plan, USAGE)
    embeddings = AsyncMock(spec=Embeddings)
    embeddings.embed.return_value = [[1.0] * 1024]
    return LearningProcessor(store, embeddings, model), store, embeddings, model


def prepare_quiz(store: AsyncMock, model: AsyncMock) -> None:
    source = Evidence(
        uuid4(),
        "The median resists extreme outliers.",
        1,
        {"kind": "page", "position": 1},
        document_id=uuid4(),
        version_id=1,
    )
    store.message_sources.return_value = [source]
    model.plan_quiz_step.side_effect = [
        ({"tool": "search", "query": "median"}, USAGE),
        ({"tool": "read_source", "source_id": str(source.id)}, USAGE),
        ({"tool": "generate_quiz"}, USAGE),
    ]
    model.generate_quiz_with_usage.return_value = (
        {
            "questions": [
                {
                    "kind": "single",
                    "topic": "statistics",
                    "stem": "Which statistic resists outliers?",
                    "options": ["Median", "Mean"],
                    "answer": "Median",
                    "explanation": "The source identifies a robust statistic.",
                    "citations": [{"source_id": str(source.id), "quote": source.content}],
                }
            ]
        },
        USAGE,
    )


def test_chat_quiz_is_validated_before_atomic_message_publication() -> None:
    worker, store, _, model = processor(
        {"action": "create_quiz", "title": "Practice", "config": CONFIG}
    )
    prepare_quiz(store, model)
    assert asyncio.run(worker.process_message())
    store.prepare_message_quiz.assert_awaited_once()
    store.finish_message_quiz.assert_awaited_once()
    args = store.finish_message_quiz.call_args.args
    assert args[3]["type_counts"] == CONFIG["type_counts"]
    assert args[4][0]["sources"][0]["version_id"] == 1
    assert args[5]["model_calls"] == 5
    assert args[5]["task_action"] == "create_quiz"
    store.finish_message.assert_not_awaited()


def test_chat_weak_practice_uses_actual_scoped_weak_topics_instead_of_model_topic() -> None:
    worker, store, _, model = processor(
        {"action": "practice_weak_topics", "title": "Targeted Practice", "config": CONFIG},
        {
            "quiz_id": str(uuid4()),
            "attempt_id": str(uuid4()),
            "title": "Past quiz",
            "weak_topics": ["robust median"],
            "summary": "A wrong answer",
        },
    )
    prepare_quiz(store, model)
    asyncio.run(worker.process_message())
    assert model.generate_quiz_with_usage.call_args.args[0]["topic"] == "robust median"


def test_review_without_submitted_practice_returns_guidance_without_generation() -> None:
    worker, store, embeddings, model = processor({"action": "review_mistakes"})
    asyncio.run(worker.process_message())
    result = store.finish_message_task.call_args.args[2]
    assert result["kind"] == "clarification" and "提交作答" in result["text"]
    embeddings.embed.assert_not_awaited()
    model.generate_quiz_with_usage.assert_not_awaited()


def test_review_links_only_the_store_selected_submitted_attempt() -> None:
    context = {
        "quiz_id": str(uuid4()),
        "attempt_id": str(uuid4()),
        "title": "Practice",
        "weak_topics": ["statistics"],
        "summary": "Mistake",
    }
    worker, store, embeddings, _ = processor({"action": "review_mistakes"}, context)
    asyncio.run(worker.process_message())
    result = store.finish_message_task.call_args.args[2]
    assert result["quiz_id"] == context["quiz_id"] and result["attempt_id"] == context["attempt_id"]
    embeddings.embed.assert_not_awaited()


def test_cancel_after_task_plan_never_creates_a_quiz_or_calls_evidence_tools() -> None:
    worker, store, embeddings, _ = processor(
        {"action": "create_quiz", "title": "Practice", "config": CONFIG}
    )
    store.ensure_message_active.side_effect = [None, RagFailure("AGENT_RUN_INACTIVE", "cancelled")]
    asyncio.run(worker.process_message())
    embeddings.embed.assert_not_awaited()
    store.prepare_message_quiz.assert_not_awaited()
    store.finish_message_quiz.assert_not_awaited()
    assert store.fail_message.call_args.args[2] == "AGENT_RUN_INACTIVE"


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
