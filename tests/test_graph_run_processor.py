"""The new graph dispatches only the validated stages in a bounded plan."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from app.learning.api import StudyAnswerResponse
from app.learning.application import LearningModel
from app.learning.graph_tasks import GraphTaskPlan
from app.learning.run_application import AgentRunProcessor
from app.learning.run_store import SqlAgentRunStore
from app.learning.workflow import GRAPH_VERSION, StudyExecutor
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
    executor = AsyncMock(spec=StudyExecutor)
    return AgentRunProcessor(store, embeddings, model, executor), store, model


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


def test_overview_summary_validates_quote_before_publication() -> None:
    worker, store, model = setup("summary", {"summary_mode": "overview"})
    source = Evidence(
        uuid4(),
        "The median resists extreme values.",
        3,
        {"kind": "page", "position": 3, "title": None, "path": []},
        document_id=uuid4(),
        version_id=1,
    )
    store.overview_sources.return_value = ([source], {"sampled_pages": 1, "indexed_pages": 44})
    model.answer_study.return_value = (
        {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "The median resists outliers.",
                    "citations": [{"source_id": str(source.id), "quote": source.content}],
                }
            ],
            "explanation": (
                "A median is the middle value after sorting, so extremes have less influence."
            ),
        },
        {**USAGE, "prompt_version": STUDY_PROMPT_VERSION},
    )
    asyncio.run(worker.process_next())
    answer = store.publish_summary.call_args.args[2]
    assert StudyAnswerResponse.model_validate(answer).explanation == answer["explanation"]
    assert answer["claims"][0]["citations"][0]["version_id"] == 1
    assert answer["explanation"].startswith("A median is the middle value")
    assert store.publish_summary.call_args.args[4] == {
        "sampled_pages": 1,
        "indexed_pages": 44,
    }
    worker, store, model = setup("summary", {"summary_mode": "overview"})
    store.overview_sources.return_value = ([source], {"sampled_pages": 1, "indexed_pages": 44})
    model.answer_study.return_value = (
        {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "Unsupported",
                    "citations": [{"source_id": str(source.id), "quote": "invented quotation"}],
                }
            ],
        },
        USAGE,
    )
    asyncio.run(worker.process_next())
    store.publish_summary.assert_not_awaited()
    store.fail.assert_awaited_once()


def test_focused_question_keeps_relevance_retrieval_path() -> None:
    worker, store, _ = setup("summary", {"summary_mode": "focused"})
    worker.executor.answer.return_value = ({"insufficient_evidence": True, "claims": []}, USAGE)
    asyncio.run(worker.process_next())
    worker.executor.answer.assert_awaited_once()
    store.overview_sources.assert_not_awaited()
    store.publish_summary.assert_awaited_once()


def test_pdf_stage_publishes_only_persisted_summary() -> None:
    worker, store, model = setup("pdf", {"steps": ["summary", "pdf"]})
    asyncio.run(worker.process_next())
    store.publish_pdf.assert_awaited_once()
    model.answer_study.assert_not_awaited()
