"""Isolated PostgreSQL/API regression checks for durable learning workflows."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.auth import Principal, get_current_principal
from app.core.database import async_session_factory as sessions
from app.core.database import close_database
from app.documents.infrastructure.models import DocumentModel, DocumentVersionModel, WorkspaceModel
from app.learning.api import get_learning_service
from app.learning.application import LearningProcessor, LearningService
from app.learning.infrastructure.langgraph_workflow import LangGraphStudyExecutor
from app.learning.models import Quiz
from app.learning.run_application import AgentRunProcessor
from app.learning.run_models import AgentRun, AgentStageExecution
from app.learning.run_store import SqlAgentRunStore
from app.learning.store import SqlLearningStore
from app.main import app
from app.rag.domain import RagFailure
from scripts.verify_learning_flow import PROFILE, FakeModels, expect_error, seed_document

CONFIG = {
    "type_counts": {"single": 1, "multiple": 0, "true_false": 0, "short": 1},
    "difficulty": "medium",
    "language": "en",
    "topic": "statistics",
}


class Models(FakeModels):
    async def plan_task(
        self, request: str, *, history: list[dict[str, str]], review_available: bool
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if request.startswith("Study"):
            return {
                "action": "study",
                "title": "Study practice",
                "config": CONFIG,
                "summary_request": "Summarize the selected materials",
                "review_after_submit": True,
                "practice_after_review": True,
            }, {"model": "fake", "prompt_tokens": 20, "completion_tokens": 5}
        if request.startswith("Unclear") and "用户补充" not in request:
            return {"action": "clarify", "message": "Which chapter?"}, {
                "model": "fake",
                "prompt_tokens": 20,
                "completion_tokens": 5,
            }
        return await super().plan_task(request, history=history, review_available=review_available)


async def verify() -> None:
    async with sessions.begin() as session:
        owner = WorkspaceModel(public_id=uuid4(), name="workflow fixture", status="active")
        foreign = WorkspaceModel(
            public_id=uuid4(), name="foreign workflow fixture", status="active"
        )
        session.add_all([owner, foreign])
        await session.flush()
    store = SqlLearningStore(sessions, PROFILE, graph_enabled=True)
    runs = SqlAgentRunStore(store)
    model = Models()
    worker = AgentRunProcessor(runs, model, model, LangGraphStudyExecutor())
    service = LearningService(store)
    first = await seed_document(owner, "workflow-a.pdf")
    second = await seed_document(owner, "workflow-b.pdf")
    await seed_document(foreign, "foreign.pdf")
    conversation = await service.create_conversation(owner.public_id, "Study", [first, second], [])
    conversation_id = UUID(conversation["id"])

    async def request(text: str) -> UUID:
        message = await service.ask(owner.public_id, conversation_id, str(uuid4()), text)
        return UUID(message["run_id"])

    async def advance(count: int = 1) -> None:
        for _ in range(count):
            assert await worker.process_next()

    try:
        run_id = await request("Study these materials")
        await advance(3)
        waiting = await runs.get(owner.public_id, run_id)
        assert waiting["status"] == "waiting_input" and waiting["stage"] == "wait"
        output = waiting["outputs"][-1]
        quiz_id, attempt_id = UUID(output["quiz_id"]), UUID(output["attempt_id"])
        attempt = await store.get_attempt(owner.public_id, quiz_id, attempt_id)
        assert all(q["answer"] is None and not q["sources"] for q in attempt["questions"])
        await expect_error(runs.get(foreign.public_id, run_id), "RESOURCE_NOT_FOUND")
        await expect_error(runs.cancel(foreign.public_id, run_id), "RESOURCE_NOT_FOUND")
        await expect_error(
            runs.respond(foreign.public_id, run_id, "foreign", 0, "answer"), "RESOURCE_NOT_FOUND"
        )
        await expect_error(
            runs.list_runs(foreign.public_id, conversation_id, 0, 10), "RESOURCE_NOT_FOUND"
        )
        await expect_error(
            store.get_attempt(foreign.public_id, quiz_id, attempt_id), "RESOURCE_NOT_FOUND"
        )
        # New scope only affects new questions. Waiting does not occupy execution resources.
        await store.set_conversation_scope(owner.public_id, conversation_id, [second], [])
        other = await request("What resists outliers?")
        await advance(2)
        assert (await runs.get(owner.public_id, other))["status"] == "completed"
        assert model.answer_document_ids == {second}
        assert len((await runs.get(owner.public_id, run_id))["scope"]) == 2
        revision = attempt["revision"]
        for q in attempt["questions"]:
            result = await store.save_answer(
                owner.public_id,
                quiz_id,
                attempt_id,
                UUID(q["id"]),
                "Mean" if q["kind"] == "single" else "A short study answer",
                revision,
            )
            revision = result["revision"]
            replay = await store.save_answer(
                owner.public_id,
                quiz_id,
                attempt_id,
                UUID(q["id"]),
                result["response"],
                revision - 1,
            )
            assert replay["revision"] == revision  # Lost acknowledgement never duplicates a write.

        await expect_error(
            store.save_answer(
                owner.public_id,
                quiz_id,
                attempt_id,
                UUID(attempt["questions"][0]["id"]),
                "Median",
                0,
            ),
            "ANSWER_REVISION_CONFLICT",
        )
        await store.submit_attempt(owner.public_id, quiz_id, attempt_id)
        await store.submit_attempt(owner.public_id, quiz_id, attempt_id)
        # A fresh processor simulates service restart; no in-memory state is required.
        worker = AgentRunProcessor(
            SqlAgentRunStore(store), Models(), Models(), LangGraphStudyExecutor()
        )
        await advance(3)  # grading, review, one weak-topic Quiz
        completed = await runs.get(owner.public_id, run_id)
        assert completed["status"] == "completed" and len(completed["outputs"]) == 4
        assert completed["outputs"][2]["attempt_id"] == str(attempt_id)
        assert (await store.get_attempt(owner.public_id, quiz_id, attempt_id))[
            "status"
        ] == "submitted"
        assert not await worker.process_next()
        async with sessions.begin() as session:
            persisted = await session.scalar(select(AgentRun).where(AgentRun.public_id == run_id))
            assert persisted is not None and persisted.usage["model_calls"] >= 10
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AgentStageExecution)
                    .where(
                        AgentStageExecution.run_id == persisted.id,
                        AgentStageExecution.result.is_not(None),
                    )
                )
                == 0
            )
        print(
            "PASS: scoped full learning, saved drafts, score event, restart "
            "and single weak-topic output"
        )

        # Read/invalid-input/authorization API contracts without paid models.
        app.dependency_overrides[get_learning_service] = lambda: service
        app.dependency_overrides[get_current_principal] = lambda: Principal(owner.public_id)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/agent-runs/{run_id}")
            assert response.status_code == 200, response.text
            assert "usage" not in response.json() and "fence" not in response.json()
            response = await client.post(
                f"/api/v1/agent-runs/{run_id}:respond",
                headers={"Idempotency-Key": "bad"},
                json={"answer": "x", "expected_revision": -1},
            )
            assert response.status_code == 422
            app.dependency_overrides[get_current_principal] = lambda: Principal(foreign.public_id)
            assert (await client.get(f"/api/v1/agent-runs/{run_id}")).status_code == 404
        app.dependency_overrides.clear()
        print("PASS: API projection, invalid input and cross-workspace rejection")

        # Clarification is bounded, idempotent, and does not change the original scope.
        unclear = await request("Unclear request")
        await advance()
        clarification = await runs.get(owner.public_id, unclear)
        assert clarification["stage"] == "clarify"
        resumed = await runs.respond(
            owner.public_id, unclear, "clarify-key", clarification["revision"], "Chapter three"
        )
        assert resumed == await runs.respond(
            owner.public_id, unclear, "clarify-key", clarification["revision"], "Chapter three"
        )
        await expect_error(
            runs.respond(
                owner.public_id,
                unclear,
                "clarify-key",
                clarification["revision"],
                "Different chapter",
            ),
            "IDEMPOTENCY_CONFLICT",
        )
        await advance(2)
        print("PASS: clarification correlation and duplicate response")

        # Result receipts survive crashes; already returned external requests are reused.
        recovering = await request("What resists outliers?")
        task = await runs.claim()
        assert task is not None and task["id"] == str(recovering)
        await runs.begin_call(recovering, task["fence"], 0, "plan_task")
        receipt = [
            {"action": "answer"},
            {"model": "fake", "prompt_tokens": 20, "completion_tokens": 5},
        ]
        await runs.finish_call(
            recovering,
            task["fence"],
            0,
            receipt,
            {"model_calls": 1, "prompt_tokens": 20, "completion_tokens": 5, "cost_units": 40},
        )
        async with sessions.begin() as session:
            await session.execute(
                update(AgentRun)
                .where(AgentRun.public_id == recovering)
                .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
            )
        await advance(2)
        assert (await runs.get(owner.public_id, recovering))["status"] == "completed"
        print("PASS: returned request receipt replays after lease expiry")

        unknown = await request("What resists outliers?")
        task = await runs.claim()
        assert task is not None
        await runs.begin_call(unknown, task["fence"], 0, "plan_task")
        async with sessions.begin() as session:
            await session.execute(
                update(AgentRun)
                .where(AgentRun.public_id == unknown)
                .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert not await worker.process_next()
        assert (await runs.get(owner.public_id, unknown))["failure_code"] == "RUN_RESULT_UNKNOWN"
        await runs.cancel(owner.public_id, unknown)
        stopped = await request("Study for cancellation")
        await advance(2)
        assert (await runs.get(owner.public_id, stopped))["outputs"]
        task = await runs.claim()
        assert task is not None
        await runs.cancel(owner.public_id, stopped)
        try:
            await runs.ensure_active(stopped, task["fence"])
            raise AssertionError("Cancelled task must not publish")
        except RagFailure:
            pass
        assert (await runs.get(owner.public_id, stopped))["outputs"]
        print(
            "PASS: unknown requests are not retried "
            "and cancellation fences preserve completed output"
        )

        exhausted = await request("What resists outliers?")
        async with sessions.begin() as session:
            await session.execute(
                update(AgentRun)
                .where(AgentRun.public_id == exhausted)
                .values(usage={"execution_seconds": 770})
            )
        assert not await worker.process_next()
        assert (await runs.get(owner.public_id, exhausted))["failure_code"] == "RUN_BUDGET_EXCEEDED"
        old = await request("What resists outliers?")
        async with sessions.begin() as session:
            await session.execute(
                update(AgentRun)
                .where(AgentRun.public_id == old)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert (await runs.get(owner.public_id, old))["status"] == "expired"
        assert not await worker.process_next()
        print("PASS: accumulated execution budget and waiting expiry")

        # Cancelling future Agent steps must leave an already published Quiz usable.
        cancelled_study = await request("Study cancellation practice")
        await advance(3)
        published = (await runs.get(owner.public_id, cancelled_study))["outputs"][-1]
        saved_quiz, saved_attempt = UUID(published["quiz_id"]), UUID(published["attempt_id"])
        await runs.cancel(owner.public_id, cancelled_study)
        original = await store.get_attempt(owner.public_id, saved_quiz, saved_attempt)
        for question in original["questions"]:
            await store.save_answer(
                owner.public_id,
                saved_quiz,
                saved_attempt,
                UUID(question["id"]),
                "Median" if question["kind"] == "single" else "Manual study answer",
            )
        await store.submit_attempt(owner.public_id, saved_quiz, saved_attempt)
        manual = LearningProcessor(store, Models(), Models())
        assert await manual.process_grading()
        assert (await store.get_attempt(owner.public_id, saved_quiz, saved_attempt))[
            "status"
        ] == "submitted"
        assert (await runs.get(owner.public_id, cancelled_study))["status"] == "cancelled"
        print("PASS: published Quiz remains usable after cancelling future Agent stages")

        class InvalidModels(Models):
            async def plan_task(
                self, request: str, *, history: list[dict[str, str]], review_available: bool
            ) -> tuple[dict[str, Any], dict[str, Any]]:
                raise RagFailure("AGENT_PLAN_INVALID", "Completed response was invalid")

        invalid = await request("Invalid provider response")
        invalid_worker = AgentRunProcessor(
            runs, Models(), InvalidModels(), LangGraphStudyExecutor()
        )
        assert await invalid_worker.process_next()
        rejected = await runs.get(owner.public_id, invalid)
        assert rejected["status"] == "failed" and rejected["failure_code"] == "AGENT_PLAN_INVALID"
        assert not await invalid_worker.process_next()
        print(
            "PASS: received invalid response fails clearly without retry or unknown billing state"
        )

        # Database constraints prevent a foreign Quiz being attached even outside API code.
        foreign_quiz = await SqlLearningStore(sessions, PROFILE).create_quiz(
            foreign.public_id,
            "foreign-quiz",
            "Foreign",
            CONFIG,
            [await seed_document(foreign, "extra.pdf")],
            [],
        )
        try:
            async with sessions.begin() as session:
                q = await session.scalar(
                    select(Quiz).where(Quiz.public_id == UUID(foreign_quiz["id"]))
                )
                assert q is not None
                await session.execute(
                    update(AgentRun).where(AgentRun.public_id == run_id).values(quiz_id=q.id)
                )
            raise AssertionError("Cross-workspace foreign key must reject")
        except IntegrityError:
            pass
        async with sessions.begin() as session:
            await session.execute(
                update(DocumentModel)
                .where(DocumentModel.workspace_id == owner.id)
                .values(active_version_id=None)
            )
            await session.execute(
                delete(DocumentVersionModel).where(DocumentVersionModel.workspace_id == owner.id)
            )
        async with sessions.begin() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AgentRun)
                    .where(AgentRun.workspace_id == owner.id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AgentStageExecution)
                    .where(AgentStageExecution.workspace_id == owner.id)
                )
                == 0
            )
        print("PASS: tenant constraints and source deletion purge all run checkpoints")
    finally:
        app.dependency_overrides.clear()
        async with sessions.begin() as session:
            await session.execute(
                update(DocumentModel)
                .where(DocumentModel.workspace_id.in_([owner.id, foreign.id]))
                .values(active_version_id=None)
            )
            await session.execute(
                delete(DocumentModel).where(DocumentModel.workspace_id.in_([owner.id, foreign.id]))
            )
            await session.execute(
                delete(WorkspaceModel).where(WorkspaceModel.id.in_([owner.id, foreign.id]))
            )
        await close_database()


def main() -> None:
    asyncio.run(verify())


if __name__ == "__main__":
    main()
