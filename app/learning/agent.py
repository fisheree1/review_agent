"""Bounded, read-only tool planning for one scoped conversation message."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.rag.domain import Evidence, RagFailure, validate_answer, validate_vectors
from app.rag.ports import Embeddings

AGENT_VERSION = "scoped-study-tools-v1"
MAX_TOOL_STEPS = 5
MAX_MODEL_CALLS = 7
MAX_SEARCHES = 2
MAX_READS = 3
MAX_BILLABLE_TOKENS = 18_000
# Output tokens carry a higher relative cost. This is not a USD estimate.
MAX_COST_UNITS = 30_000


class ToolDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Literal["search", "read_source", "answer", "generate_quiz"]
    query: str | None = Field(default=None, max_length=300)
    source_id: UUID | None = None

    @model_validator(mode="after")
    def validate_arguments(self) -> Self:
        if self.tool == "search":
            if self.query is None or not self.query.strip() or self.source_id is not None:
                raise ValueError("Search requires only a nonempty query")
            self.query = self.query.strip()
        elif self.tool == "read_source":
            if self.source_id is None or self.query is not None:
                raise ValueError("Read requires only a source ID")
        elif self.query is not None or self.source_id is not None:
            raise ValueError("Answer has no tool arguments")
        return self


class PlanningModel(Protocol):
    async def plan_step(
        self,
        question: str,
        *,
        history: list[dict[str, str]],
        observations: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...

    async def answer(
        self, question: str, sources: list[Evidence], *, history: list[dict[str, str]] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...


@dataclass
class AgentBudget:
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_units: int = 0

    def charge(self, usage: dict[str, Any]) -> None:
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if type(prompt) is not int or type(completion) is not int or prompt < 1 or completion < 1:
            raise RagFailure("AGENT_USAGE_INVALID", "模型未返回可靠的计费信息")
        self.model_calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cost_units += prompt + 4 * completion
        if (
            self.model_calls > MAX_MODEL_CALLS
            or self.prompt_tokens + self.completion_tokens > MAX_BILLABLE_TOKENS
            or self.cost_units > MAX_COST_UNITS
        ):
            raise RagFailure("AGENT_BUDGET_EXCEEDED", "本次问答已达到模型调用预算")

    def usage(
        self,
        last: dict[str, Any],
        *,
        steps: int,
        searches: int,
        reads: int,
        planning_prompt_version: str | None,
        trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            **last,
            "agent_version": AGENT_VERSION,
            "planning_prompt_version": planning_prompt_version,
            "tool_steps": steps,
            "search_calls": searches,
            "read_calls": reads,
            "model_calls": self.model_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_units": self.cost_units,
            "tool_trace": trace,
        }


SourceSearch = Callable[[list[float], str], Awaitable[list[Evidence]]]
RunCheck = Callable[[], Awaitable[None]]
PlanStep = Callable[[list[dict[str, Any]]], Awaitable[tuple[dict[str, Any], dict[str, Any]]]]


@dataclass
class EvidencePlan:
    sources: list[Evidence]
    budget: AgentBudget
    last_usage: dict[str, Any]
    steps: int
    searches: int
    reads: int
    trace: list[dict[str, Any]]

    def usage(self, last: dict[str, Any], version: str) -> dict[str, Any]:
        return {
            **self.budget.usage(
                last,
                steps=self.steps,
                searches=self.searches,
                reads=self.reads,
                planning_prompt_version=self.last_usage.get("prompt_version"),
                trace=self.trace,
            ),
            "agent_version": version,
        }


async def run_scoped_agent(
    question: str,
    history: list[dict[str, str]],
    *,
    embeddings: Embeddings,
    model: PlanningModel,
    search: SourceSearch,
    ensure_active: RunCheck | None = None,
    budget: AgentBudget | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Let the model choose read-only tools; application code owns scope and publication."""

    async def decide(observations: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        return await model.plan_step(question, history=history, observations=observations)

    plan = await plan_evidence(
        decide,
        "answer",
        embeddings=embeddings,
        search=search,
        ensure_active=ensure_active,
        budget=budget,
    )
    if not plan.sources:
        answer = {"insufficient_evidence": True, "claims": []}
        plan.trace.append({"tool": "answer", "insufficient_evidence": True})
        return answer, plan.usage(plan.last_usage, AGENT_VERSION)
    if ensure_active is not None:
        await ensure_active()
    payload, usage = await model.answer(question, plan.sources, history=history)
    plan.budget.charge(usage)
    answer = validate_answer(payload, plan.sources)
    plan.trace.append({"tool": "answer", "insufficient_evidence": answer["insufficient_evidence"]})
    return answer, plan.usage(usage, AGENT_VERSION)


async def plan_evidence(
    decide: PlanStep,
    terminal_tool: Literal["answer", "generate_quiz"],
    *,
    embeddings: Embeddings,
    search: SourceSearch,
    ensure_active: RunCheck | None,
    budget: AgentBudget | None = None,
) -> EvidencePlan:
    """Collect evidence with the same bounded tool policy for answers and Quiz candidates."""
    budget = budget if budget is not None else AgentBudget()
    observations: list[dict[str, Any]] = []
    discovered: dict[UUID, Evidence] = {}
    selected: dict[UUID, Evidence] = {}
    searched_queries: set[str] = set()
    searches = 0
    reads = 0
    last_usage: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    for step in range(1, MAX_TOOL_STEPS + 1):
        if ensure_active is not None:
            await ensure_active()
        if budget.model_calls >= MAX_MODEL_CALLS:
            raise RagFailure("AGENT_BUDGET_EXCEEDED", "本次问答已达到模型调用预算")
        raw_decision, last_usage = await decide(observations)
        budget.charge(last_usage)
        try:
            decision = ToolDecision.model_validate(raw_decision)
        except ValidationError as exc:
            raise RagFailure("AGENT_PLAN_INVALID", "Agent 工具选择无效") from exc
        if decision.tool not in ("search", "read_source", terminal_tool):
            raise RagFailure("AGENT_TOOL_INVALID", "本次任务不允许调用该工具")
        if ensure_active is not None:
            await ensure_active()

        if decision.tool == "search":
            query = decision.query
            if query is None:
                raise RagFailure("AGENT_PLAN_INVALID", "Agent 工具选择无效")
            normalized = query.casefold()
            if searches >= MAX_SEARCHES or normalized in searched_queries:
                raise RagFailure("AGENT_TOOL_INVALID", "Agent 重复或超量搜索")
            searches += 1
            searched_queries.add(normalized)
            vectors = await embeddings.embed([query], query=True)
            validate_vectors(vectors, 1)
            matches = await search(vectors[0], query)
            for source in matches[:8]:
                discovered[source.id] = source
            trace.append({"tool": "search", "matches": min(len(matches), 8)})
            observations.append(
                {
                    "tool": "search",
                    "sources": [
                        {
                            "source_id": str(source.id),
                            "document_id": str(source.document_id),
                            "preview": source.content[:180],
                        }
                        for source in matches[:8]
                    ],
                }
            )
        elif decision.tool == "read_source":
            source_id = decision.source_id
            if source_id is None:
                raise RagFailure("AGENT_PLAN_INVALID", "Agent 工具选择无效")
            if source_id not in discovered or source_id in selected or reads >= MAX_READS:
                raise RagFailure("AGENT_TOOL_INVALID", "Agent 请求的来源不在本次检索结果中")
            source = discovered[source_id]
            selected[source_id] = source
            reads += 1
            trace.append({"tool": "read_source", "source_id": str(source_id)})
            observations.append(
                {"tool": "read_source", "source_id": str(source_id), "content": source.content}
            )
        else:
            if searches == 0:
                raise RagFailure("AGENT_TOOL_INVALID", "Agent 尚未检索所选资料")
            if budget.model_calls >= MAX_MODEL_CALLS:
                raise RagFailure("AGENT_BUDGET_EXCEEDED", "本次问答已达到模型调用预算")
            return EvidencePlan(
                list(selected.values()), budget, last_usage, step, searches, reads, trace
            )
    raise RagFailure("AGENT_STEP_LIMIT", "Agent 未在步骤上限内完成回答")
