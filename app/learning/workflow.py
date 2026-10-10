"""Versioned learning plans and execution ports, independent of graph/database SDKs."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from app.learning.agent import AgentBudget, RunCheck, SourceSearch, StudyPlanningModel
from app.learning.conversation_tasks import TaskPlan
from app.learning.graph_tasks import GraphTaskPlan
from app.learning.quiz_agent import QuizPlanningModel
from app.rag.domain import Evidence
from app.rag.ports import Embeddings

LEGACY_GRAPH_VERSION = "study-graph-v1"
BATCHED_GRAPH_VERSIONS = ("study-graph-v4", "study-graph-v5")
GRAPH_VERSION = "study-graph-v5"
FAST_GRAPH_VERSIONS = ("study-graph-v3", *BATCHED_GRAPH_VERSIONS)
PLANNED_GRAPH_VERSION = "study-graph-v2"
COMPOSABLE_GRAPH_VERSIONS = (PLANNED_GRAPH_VERSION, *FAST_GRAPH_VERSIONS)
SUPPORTED_GRAPH_VERSIONS = (LEGACY_GRAPH_VERSION, *COMPOSABLE_GRAPH_VERSIONS)
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "expired"})
MAX_RUN_CALLS = 22
MAX_RUN_TOKENS = 54_000
MAX_RUN_COST_UNITS = 90_000
MAX_RUN_SECONDS = 770
MAX_OVERVIEW_RUN_SECONDS = 1540


class StudyExecutor(Protocol):
    async def answer(
        self,
        question: str,
        history: list[dict[str, str]],
        *,
        embeddings: Embeddings,
        model: StudyPlanningModel,
        search: SourceSearch,
        ensure_active: RunCheck,
        budget: AgentBudget,
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...

    async def quiz(
        self,
        config: dict[str, Any],
        *,
        embeddings: Embeddings,
        model: QuizPlanningModel,
        search: SourceSearch,
        ensure_active: RunCheck,
        budget: AgentBudget,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...


class AgentRunPersistence(Protocol):
    async def claim(self) -> dict[str, Any] | None: ...
    async def ensure_active(self, public_id: UUID, fence: UUID) -> None: ...
    async def sources(
        self, public_id: UUID, fence: UUID, vector: list[float], query: str
    ) -> list[Evidence]: ...
    async def overview_sources(
        self, public_id: UUID, fence: UUID
    ) -> tuple[list[Evidence], dict[str, int]]: ...
    async def overview_batch(
        self, public_id: UUID, fence: UUID
    ) -> tuple[list[Evidence], dict[str, int]]: ...
    async def begin_call(self, public_id: UUID, fence: UUID, ordinal: int, kind: str) -> Any: ...
    async def finish_call(
        self, public_id: UUID, fence: UUID, ordinal: int, result: Any, usage: dict[str, Any]
    ) -> None: ...
    async def reject_call(self, public_id: UUID, fence: UUID, ordinal: int) -> None: ...
    async def record_answer_validation(
        self, public_id: UUID, fence: UUID, attempt: int, diagnosis: dict[str, Any]
    ) -> None: ...
    async def save_plan(
        self, public_id: UUID, fence: UUID, plan: TaskPlan | GraphTaskPlan
    ) -> None: ...
    async def reserve_quiz(self, public_id: UUID, fence: UUID) -> None: ...
    async def publish_summary(
        self,
        public_id: UUID,
        fence: UUID,
        answer: dict[str, Any],
        usage: dict[str, Any],
        coverage: dict[str, int] | None = None,
    ) -> None: ...
    async def publish_pdf(self, public_id: UUID, fence: UUID) -> None: ...
    async def publish_quiz(
        self,
        public_id: UUID,
        fence: UUID,
        config: dict[str, Any],
        questions: list[dict[str, Any]],
        usage: dict[str, Any],
    ) -> None: ...
    async def review(self, public_id: UUID, fence: UUID) -> dict[str, Any] | None: ...
    async def publish_review(
        self, public_id: UUID, fence: UUID, review: dict[str, Any] | None
    ) -> None: ...
    async def grading_items(
        self, public_id: UUID, fence: UUID
    ) -> tuple[UUID, list[dict[str, Any]]]: ...
    async def finish_grading(
        self, public_id: UUID, fence: UUID, grades: list[dict[str, Any]]
    ) -> None: ...
    async def fail(self, public_id: UUID, fence: UUID, code: str) -> None: ...
    async def record_duration(self, public_id: UUID, seconds: float) -> None: ...


def run_seconds_limit(graph_version: str | None) -> int:
    return MAX_OVERVIEW_RUN_SECONDS if graph_version in BATCHED_GRAPH_VERSIONS else MAX_RUN_SECONDS


def check_run_budget(usage: dict[str, Any], graph_version: str | None = None) -> None:
    from app.rag.domain import RagFailure

    if (
        usage.get("model_calls", 0) > MAX_RUN_CALLS
        or usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0) > MAX_RUN_TOKENS
        or usage.get("cost_units", 0) > MAX_RUN_COST_UNITS
        or usage.get("execution_seconds", 0) > run_seconds_limit(graph_version)
    ):
        raise RagFailure("RUN_BUDGET_EXCEEDED", "学习任务已达到累计预算")
