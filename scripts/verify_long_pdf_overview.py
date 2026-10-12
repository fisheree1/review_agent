"""Synthetic scoped long-PDF coverage and crash recovery on an isolated migrated DB."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update

from app.core.database import async_session_factory as sessions
from app.core.database import close_database
from app.documents.infrastructure.models import (
    DocumentModel,
    DocumentPageModel,
    DocumentVersionModel,
    WorkspaceModel,
)
from app.learning.agent import AgentBudget
from app.learning.application import LearningService
from app.learning.graph_tasks import GraphTaskPlan
from app.learning.infrastructure.langgraph_workflow import LangGraphStudyExecutor
from app.learning.models import ConversationMessage
from app.learning.run_application import AgentRunProcessor, RecordedCalls, RecordedModels
from app.learning.run_models import AgentRun, AgentStageExecution
from app.learning.run_store import SqlAgentRunStore
from app.learning.store import SqlLearningStore
from app.learning.study_generation import generate_study
from app.rag.domain import STUDY_PROMPT_VERSION, Evidence, RagFailure
from app.rag.models import DocumentChunk, DocumentIndex
from scripts.verify_learning_flow import PROFILE, VECTOR, FakeModels, expect_error, seed_document


class Model(FakeModels):
    def __init__(self, selected: UUID) -> None:
        super().__init__()
        self.selected = selected
        self.calls = 0

    async def answer_study(
        self,
        question: str,
        sources: list[Evidence],
        *,
        history: Any = None,
        mode: Literal["focused", "overview"] = "focused",
        repair_feedback: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.calls += 1
        assert 1 <= len(sources) <= 8
        assert all(source.document_id == self.selected for source in sources)
        return {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": f"Knowledge on page {source.unit}",
                    "title": f"Point {source.unit}",
                    "explanation": f"Teaching for page {source.unit}",
                    "citations": [{"source_id": str(source.id), "quote": source.content}],
                }
                for source in sources
            ],
        }, {
            "model": "fixture",
            "prompt_version": STUDY_PROMPT_VERSION,
            "prompt_tokens": 100,
            "completion_tokens": 40,
        }


async def verify() -> None:
    async with sessions.begin() as session:
        if await session.scalar(select(WorkspaceModel.id).limit(1)) is not None:
            raise RuntimeError("Use an empty, disposable migrated database")
        owner, foreign = (
            WorkspaceModel(name="overview fixture"),
            WorkspaceModel(name="foreign overview"),
        )
        session.add_all([owner, foreign])
        await session.flush()
    selected = await seed_document(owner, "long.pdf")
    await seed_document(owner, "deselected.pdf")
    await seed_document(foreign, "foreign.pdf")
    async with sessions.begin() as session:
        doc = await session.scalar(
            select(DocumentModel).where(
                DocumentModel.public_id == selected, DocumentModel.workspace_id == owner.id
            )
        )
        assert doc is not None
        version = doc.active_version_id
        index_id = await session.scalar(
            select(DocumentIndex.id).where(
                DocumentIndex.workspace_id == owner.id, DocumentIndex.document_version_id == version
            )
        )
        doc.page_count = 72
        await session.execute(
            update(DocumentVersionModel)
            .where(
                DocumentVersionModel.workspace_id == owner.id, DocumentVersionModel.id == version
            )
            .values(page_count=72)
        )
        for page in range(2, 73):
            content = f"Unique knowledge point on page {page}; assumptions and examples."
            session.add(
                DocumentPageModel(
                    workspace_id=owner.id,
                    document_version_id=version,
                    page_number=page,
                    content=content,
                    char_count=len(content),
                    locator_kind="page",
                    locator_position=page,
                    locator_path=[],
                )
            )
            session.add(
                DocumentChunk(
                    workspace_id=owner.id,
                    index_id=index_id,
                    ordinal=page,
                    unit=page,
                    start_offset=0,
                    end_offset=len(content),
                    content=content,
                    embedding=VECTOR,
                )
            )
    learning = SqlLearningStore(sessions, PROFILE, graph_enabled=True)
    service = LearningService(learning)
    runs = SqlAgentRunStore(learning)
    conversation = await service.create_conversation(owner.public_id, "Overview", [selected], [])
    model = Model(selected)
    worker = AgentRunProcessor(runs, model, model, LangGraphStudyExecutor())

    async def new_run() -> UUID:
        message = await service.ask(
            owner.public_id, UUID(conversation["id"]), str(uuid4()), "整理全文知识点并导出 PDF"
        )
        run = UUID(message["run_id"])
        task = await runs.claim()
        assert task is not None and task["id"] == str(run)
        await runs.save_plan(
            run,
            task["fence"],
            GraphTaskPlan(
                steps=["summary", "pdf"], summary_request="整理全部知识点", summary_mode="overview"
            ),
        )
        return run

    run = await new_run()
    await expect_error(runs.get(foreign.public_id, run), "RESOURCE_NOT_FOUND")
    task = await runs.claim()
    assert task is not None
    sources, coverage = await runs.overview_batch(run, task["fence"])
    assert coverage["indexed_pages"] == 72 and coverage["total_batches"] == 8
    assert min(s.unit for s in sources) == 1 and max(s.unit for s in sources) == 72
    # Simulate a crash after provider result was recorded, before batch publication.
    await RecordedModels(model, RecordedCalls(runs, run, task["fence"])).answer_study(
        "Overview", sources
    )
    async with sessions.begin() as session:
        await session.execute(
            update(AgentRun)
            .where(AgentRun.workspace_id == owner.id, AgentRun.public_id == run)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    await worker.process_next()
    assert model.calls == 1
    progress = await runs.get(owner.public_id, run)
    assert progress["stage"] == "overview_1" and progress["status"] == "queued"
    assert progress["outputs"][0]["coverage"]["processed_chunks"] == 8
    for _ in range(8):
        assert await worker.process_next()
    result = await runs.get(owner.public_id, run)
    assert result["status"] == "completed", result
    summary = next(item for item in result["outputs"] if item["kind"] == "summary")
    assert summary["coverage"]["sampled_pages"] == 64
    assert summary["coverage"]["full_pages"] == 64
    assert summary["coverage"]["knowledge_points"] == 64
    assert model.calls == 8
    title, answer, scope, pdf_coverage = await runs.pdf_material(owner.public_id, run)
    assert pdf_coverage is not None
    assert len(answer["claims"]) == 64 and len(scope) == 1
    async with sessions.begin() as session:
        receipts = list(
            await session.scalars(
                select(AgentStageExecution)
                .join(AgentRun, AgentRun.id == AgentStageExecution.run_id)
                .where(AgentRun.public_id == run, AgentStageExecution.workspace_id == owner.id)
            )
        )
        assert all(receipt.result is None and receipt.status == "published" for receipt in receipts)
    # Budget exhaustion after a batch must retain usable notes with accurate partial coverage.
    limited = await new_run()
    task = await runs.claim()
    assert task is not None
    sources, coverage = await runs.overview_batch(limited, task["fence"])
    raw, usage = await RecordedModels(
        model, RecordedCalls(runs, limited, task["fence"])
    ).answer_study("Overview", sources)
    from app.rag.domain import validate_study_answer

    answer = validate_study_answer(raw, sources, require_sections=True)
    async with sessions.begin() as session:
        row = await session.scalar(
            select(AgentRun).where(AgentRun.workspace_id == owner.id, AgentRun.public_id == limited)
        )
        assert row is not None
        row.usage = {**row.usage, "cost_units": 65000}
    await runs.publish_summary(limited, task["fence"], answer, usage, coverage)
    limited_result = await runs.get(owner.public_id, limited)
    assert limited_result["stage"] == "pdf"
    assert limited_result["outputs"][0]["coverage"]["budget_limited"] == 1
    assert limited_result["outputs"][0]["coverage"]["processed_chunks"] == 8
    await worker.process_next()
    cancelled = await new_run()
    await worker.process_next()
    before = model.calls
    await runs.cancel(owner.public_id, cancelled)
    assert not await worker.process_next()
    assert model.calls == before
    detail = await learning.get_conversation(owner.public_id, UUID(conversation["id"]))
    assert len(detail["messages"][-1]["answer"]["claims"]) == 8

    # Replay a known invalid response and its successful repair after a crash.
    # Neither request can be billed again; diagnosis must remain metadata only.
    class RepairModel(Model):
        async def answer_study(
            self,
            question: str,
            sources: list[Evidence],
            *,
            history: Any = None,
            mode: Literal["focused", "overview"] = "focused",
            repair_feedback: dict[str, Any] | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            raw, usage = await super().answer_study(question, sources, history=history, mode=mode)
            if self.calls == 1:
                raw["claims"][0]["citations"][0]["quote"] = "invented synthetic quote"
            return raw, usage

    repaired = await new_run()
    task = await runs.claim()
    assert task is not None and task["id"] == str(repaired)
    sources, coverage = await runs.overview_batch(repaired, task["fence"])
    repair_model = RepairModel(selected)

    async def note(attempt: int, diagnosis: dict[str, Any]) -> None:
        await runs.record_answer_validation(repaired, task["fence"], attempt, diagnosis)

    async def active() -> None:
        await runs.ensure_active(repaired, task["fence"])

    for _ in range(2):
        answer, usage = await generate_study(
            "Overview",
            sources,
            [],
            model=RecordedModels(repair_model, RecordedCalls(runs, repaired, task["fence"])),
            budget=AgentBudget(),
            ensure_active=active,
            mode="overview",
            record_validation=note,
        )
    assert repair_model.calls == 2
    assert usage["repair_calls"] == 1
    try:
        await runs.record_answer_validation(repaired, uuid4(), 0, {"reason": "answer_shape"})
    except RagFailure as exc:
        assert exc.code == "AGENT_RUN_INACTIVE"
    else:
        raise AssertionError("Foreign fence accepted")
    try:
        await runs.record_answer_validation(
            repaired,
            task["fence"],
            0,
            {
                "reason": "answer_shape",
                "provider_payload": "must never be stored",
            },
        )
    except RagFailure as exc:
        assert exc.code == "ANSWER_INVALID"
    else:
        raise AssertionError("Private payload accepted into diagnosis")
    async with sessions() as session:
        row = await session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == owner.id,
                AgentRun.public_id == repaired,
            )
        )
        assert row is not None and row.usage["model_calls"] == 2
        assert row.usage["answer_validation"] == [
            {
                "stage": "summary",
                "attempt": 0,
                "reason": "citation_quote_not_exact",
                "point": 1,
                "citation": 1,
            }
        ]
    await runs.publish_summary(repaired, task["fence"], answer, usage, coverage)
    await runs.cancel(owner.public_id, repaired)
    # Source deletion removes partial and final summaries through existing cascades.
    async with sessions.begin() as session:
        await session.execute(
            update(DocumentModel)
            .where(DocumentModel.workspace_id == owner.id, DocumentModel.public_id == selected)
            .values(active_version_id=None)
        )
        await session.execute(
            delete(DocumentVersionModel).where(
                DocumentVersionModel.workspace_id == owner.id, DocumentVersionModel.id == version
            )
        )
        assert not list(
            await session.scalars(
                select(ConversationMessage.id).where(ConversationMessage.workspace_id == owner.id)
            )
        )
    await close_database()
    print(
        "PASS: 64/72 pages; 8 bounded model calls; source isolation; receipt replay; "
        "partial budget result; cancellation; deletion; repair replay; sanitized diagnosis; fencing"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(verify())
