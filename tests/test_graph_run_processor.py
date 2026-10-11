"""The new graph dispatches only the validated stages in a bounded plan."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.learning.application import LearningModel
from app.learning.graph_tasks import GraphTaskPlan
from app.learning.run_application import AgentRunProcessor
from app.learning.run_store import SqlAgentRunStore
from app.learning.workflow import GRAPH_VERSION
from app.rag.domain import STUDY_PROMPT_VERSION, Evidence
from app.rag.ports import Embeddings

USAGE = {"model": "fake", "prompt_tokens": 20, "completion_tokens": 5}


def setup(stage: str, plan: dict | None = None):
    run_id, fence = uuid4(), uuid4()
    store = AsyncMock(spec=SqlAgentRunStore)
    store.claim.return_value = {
        "id": str(run_id),
        "fence": fence,
        "graph_version": GRAPH_VERSION,
        "stage": stage,
        "request": "整理并下载知识点 PDF",
        "response": None,
        "history": [],
        "memory_summary": "偏好中文",
        "plan": plan,
    }
    store.begin_call.return_value = None
    store.review.return_value = None
    model = AsyncMock(spec=LearningModel)
    embeddings = AsyncMock(spec=Embeddings)
    return AgentRunProcessor(store, embeddings, model), store, model


def test_graph_planner_uses_same_scope_memory_and_validates_composable_steps() -> None:
    worker, store, model = setup("plan")
    model.plan_graph_task.return_value = (
        {
            "steps": ["summary", "pdf"],
            "summary_request": "整理知识点",
            "summary_mode": "overview",
            "title": None,
            "config": None,
            "clarification": None,
            "memory_summary": "偏好中文",
        },
        USAGE,
    )
    asyncio.run(worker.process_next())
    model.plan_graph_task.assert_awaited_once_with(
        "整理并下载知识点 PDF", history=[], review_available=False, memory_summary="偏好中文"
    )
    plan = store.save_plan.call_args.args[2]
    assert isinstance(plan, GraphTaskPlan) and plan.steps == ["summary", "pdf"]
    store.publish_pdf.assert_not_awaited()


@pytest.mark.parametrize("version", ["study-graph-v2", "study-graph-v3", "study-graph-v4"])
def test_unsupported_versions_fail_without_model_calls(version: str) -> None:
    # Only the production legacy version and the current version keep executable paths.
    worker, store, model = setup("summary", {"summary_mode": "overview"})
    store.claim.return_value["graph_version"] = version
    asyncio.run(worker.process_next())
    assert store.fail.call_args.args[2] == "RUN_VERSION_UNSUPPORTED"
    model.answer_study.assert_not_awaited()
    worker.embeddings.embed.assert_not_awaited()
    store.publish_summary.assert_not_awaited()


def test_legacy_runs_cannot_reach_composable_only_stages() -> None:
    worker, store, _ = setup("pdf", {"action": "answer"})
    store.claim.return_value["graph_version"] = "study-graph-v1"
    asyncio.run(worker.process_next())
    store.publish_pdf.assert_not_awaited()
    assert store.fail.call_args.args[2] == "RUN_VERSION_UNSUPPORTED"


def test_legacy_focused_question_resumes_on_deterministic_retrieval() -> None:
    # In-flight production v1 runs no longer need the removed model-driven tool loop.
    worker, store, model = setup("summary", {"action": "answer"})
    store.claim.return_value["graph_version"] = "study-graph-v1"
    worker.embeddings.embed.return_value = [[1.0] + [0.0] * 1023]
    store.sources.return_value = []
    asyncio.run(worker.process_next())
    store.fail.assert_not_awaited()
    model.answer_study.assert_not_awaited()
    assert store.publish_summary.call_args.args[2]["insufficient_evidence"] is True


def test_pdf_stage_publishes_only_persisted_summary() -> None:
    worker, store, model = setup("pdf", {"steps": ["summary", "pdf"]})
    asyncio.run(worker.process_next())
    store.publish_pdf.assert_awaited_once()
    model.answer_study.assert_not_awaited()


def test_v3_focused_stage_bypasses_tool_planning_and_uses_recorded_calls() -> None:
    worker, store, model = setup(
        "summary", {"summary_mode": "focused", "summary_request": "Median"}
    )
    worker.embeddings.embed.return_value = [[1.0] + [0.0] * 1023]
    store.sources.return_value = []
    asyncio.run(worker.process_next())
    store.publish_summary.assert_awaited_once()
    assert store.begin_call.call_args.args[3] == "embed"
    assert store.publish_summary.call_args.args[3]["graph_version"] == GRAPH_VERSION


def test_overview_resumes_one_bounded_batch_and_keeps_its_citations() -> None:
    worker, store, model = setup("overview_2", {"summary_mode": "overview"})
    source = Evidence(
        uuid4(),
        "The median resists extreme values.",
        120,
        {"kind": "page", "position": 120},
        document_id=uuid4(),
        version_id=1,
    )
    coverage = {
        "sampled_pages": 24,
        "indexed_pages": 200,
        "completed_batches": 3,
        "total_batches": 8,
        "processed_chunks": 24,
        "indexed_chunks": 300,
        "full_pages": 15,
    }
    store.overview_batch.return_value = ([source], coverage)
    model.answer_study.return_value = (
        {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "Median is robust",
                    "title": "Median",
                    "explanation": "The middle value is less affected by extremes.",
                    "citations": [{"source_id": str(source.id), "quote": source.content}],
                }
            ],
            "explanation": "The middle value is less affected by extremes.",
        },
        {**USAGE, "prompt_version": STUDY_PROMPT_VERSION},
    )
    asyncio.run(worker.process_next())
    store.fail.assert_not_awaited()
    model.answer_study.assert_awaited_once()
    assert store.publish_summary.call_args.args[2]["claims"][0]["citations"][0]["unit"] == 120
    assert store.publish_summary.call_args.args[4] == coverage
    assert model.answer_study.call_args.kwargs["mode"] == "overview"


QUIZ_PLAN = {
    "steps": ["quiz"],
    "title": "Practice",
    "config": {
        "type_counts": {"single": 1, "multiple": 0, "true_false": 0, "short": 0},
        "difficulty": "medium",
        "language": "en",
        "topic": "",
    },
}


def test_quiz_stage_uses_one_generation_on_application_selected_evidence() -> None:
    worker, store, model = setup("quiz", QUIZ_PLAN)
    source = Evidence(
        uuid4(),
        "The median resists extreme values.",
        2,
        {"kind": "page", "position": 2, "title": None, "path": []},
        document_id=uuid4(),
        version_id=1,
    )
    store.quiz_sources.return_value = [source]
    model.generate_quiz_with_usage.return_value = (
        {
            "questions": [
                {
                    "kind": "single",
                    "topic": "Median",
                    "stem": "Which statistic resists extreme values?",
                    "options": ["Median", "Mean"],
                    "answer": "Median",
                    "explanation": "The median depends on order, not magnitude.",
                    "citations": [{"source_id": str(source.id), "quote": source.content}],
                }
            ]
        },
        USAGE,
    )
    asyncio.run(worker.process_next())
    store.fail.assert_not_awaited()
    # No topic: whole-scope sampling, so no embedding call and no model-chosen tools.
    worker.embeddings.embed.assert_not_awaited()
    store.quiz_sources.assert_awaited_once()
    assert store.quiz_sources.call_args.args[2:] == (None, "")
    model.generate_quiz_with_usage.assert_awaited_once()
    assert [call.args[3] for call in store.begin_call.call_args_list] == ["generate_quiz"]
    questions, usage = store.publish_quiz.call_args.args[3:5]
    assert len(questions) == 1 and usage["model_calls"] == 1


def test_stage_reports_measured_time_against_its_own_reservation() -> None:
    worker, store, _ = setup("pdf", {"steps": ["summary", "pdf"]})
    store.claim.return_value["reserved_seconds"] = 15
    asyncio.run(worker.process_next())
    run_id, seconds, reserved = store.record_duration.call_args.args
    assert reserved == 15 and 0 <= seconds < 15
