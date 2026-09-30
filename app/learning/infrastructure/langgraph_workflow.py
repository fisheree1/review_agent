"""Explicit bounded graph; transient evidence never enters durable graph state."""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import ValidationError

from app.learning.agent import (
    MAX_MODEL_CALLS,
    MAX_READS,
    MAX_SEARCHES,
    MAX_TOOL_STEPS,
    AgentBudget,
    PlanStep,
    RunCheck,
    SourceSearch,
    StudyPlanningModel,
    ToolDecision,
)
from app.learning.domain import validate_candidates
from app.learning.quiz_agent import QuizPlanningModel
from app.learning.workflow import GRAPH_VERSION
from app.rag.domain import (
    STUDY_PROMPT_VERSION,
    Evidence,
    RagFailure,
    validate_study_answer,
    validate_vectors,
)
from app.rag.ports import Embeddings


class EvidenceState(TypedDict):
    observations: list[dict[str, Any]]
    discovered: dict[UUID, Evidence]
    selected: dict[UUID, Evidence]
    queries: set[str]
    steps: int
    searches: int
    reads: int
    decision: ToolDecision | None
    trace: list[dict[str, Any]]
    last_usage: dict[str, Any]
    planning_prompt_version: str | None
    payload: dict[str, Any]
    result: Any


class LangGraphStudyExecutor:
    async def _execute(
        self,
        *,
        decide: PlanStep,
        terminal: str,
        embeddings: Embeddings,
        search: SourceSearch,
        ensure_active: RunCheck,
        budget: AgentBudget,
        generate: PlanStep,
        config: dict[str, Any] | None,
    ) -> tuple[Any, dict[str, Any]]:
        async def plan(state: EvidenceState) -> dict[str, Any]:
            await ensure_active()
            if state["steps"] >= MAX_TOOL_STEPS or budget.model_calls >= MAX_MODEL_CALLS:
                raise RagFailure("AGENT_STEP_LIMIT", "工具规划达到上限")
            payload, usage = await decide(state["observations"])
            budget.charge(usage)
            try:
                decision = ToolDecision.model_validate(payload)
            except ValidationError as exc:
                raise RagFailure("AGENT_PLAN_INVALID", "工具参数无效") from exc
            if decision.tool not in ("search", "read_source", terminal):
                raise RagFailure("AGENT_TOOL_INVALID", "工具不在允许范围")
            return {
                "decision": decision,
                "steps": state["steps"] + 1,
                "last_usage": usage,
                "planning_prompt_version": usage.get("prompt_version"),
            }

        async def search_node(state: EvidenceState) -> dict[str, Any]:
            await ensure_active()
            decision = state["decision"]
            assert decision is not None and decision.query is not None
            normalized = decision.query.casefold()
            if state["searches"] >= MAX_SEARCHES or normalized in state["queries"]:
                raise RagFailure("AGENT_TOOL_INVALID", "重复或超量搜索")
            vectors = await embeddings.embed([decision.query], query=True)
            validate_vectors(vectors, 1)
            matches = (await search(vectors[0], decision.query))[:8]
            return {
                "discovered": {**state["discovered"], **{item.id: item for item in matches}},
                "queries": state["queries"] | {normalized},
                "searches": state["searches"] + 1,
                "trace": [*state["trace"], {"tool": "search", "matches": len(matches)}],
                "observations": [
                    *state["observations"],
                    {
                        "tool": "search",
                        "query": decision.query,
                        "sources": [
                            {
                                "source_id": str(s.id),
                                "document_id": str(s.document_id),
                                "preview": s.content[:180],
                            }
                            for s in matches
                        ],
                    },
                ],
            }

        async def read_node(state: EvidenceState) -> dict[str, Any]:
            await ensure_active()
            decision = state["decision"]
            assert decision is not None
            source_id = decision.source_id
            if (
                source_id not in state["discovered"]
                or source_id in state["selected"]
                or state["reads"] >= MAX_READS
            ):
                raise RagFailure("AGENT_TOOL_INVALID", "来源不在检索范围或超量读取")
            assert source_id is not None
            source = state["discovered"][source_id]
            return {
                "selected": {**state["selected"], source_id: source},
                "reads": state["reads"] + 1,
                "trace": [*state["trace"], {"tool": "read_source", "source_id": str(source_id)}],
                "observations": [
                    *state["observations"],
                    {"tool": "read_source", "source_id": str(source_id), "content": source.content},
                ],
            }

        async def generate_node(state: EvidenceState) -> dict[str, Any]:
            await ensure_active()
            if not state["searches"]:
                raise RagFailure("AGENT_TOOL_INVALID", "必须先搜索资料")
            if not state["selected"]:
                if terminal == "generate_quiz":
                    raise RagFailure("QUIZ_NO_EVIDENCE", "没有可用出题证据")
                return {"payload": {"insufficient_evidence": True, "claims": []}}
            if budget.model_calls >= MAX_MODEL_CALLS:
                raise RagFailure("AGENT_BUDGET_EXCEEDED", "模型调用预算不足")
            # Evidence stays in this invocation's closure, never in the durable run ledger.
            payload, usage = await generate([{"sources": list(state["selected"].values())}])
            budget.charge(usage)
            return {"payload": payload, "last_usage": usage}

        async def validate_node(state: EvidenceState) -> dict[str, Any]:
            await ensure_active()
            sources = list(state["selected"].values())
            if config is None:
                result: Any = validate_study_answer(
                    state["payload"],
                    sources,
                    require_explanation=state["last_usage"].get("prompt_version")
                    == STUDY_PROMPT_VERSION,
                )
            else:
                result = validate_candidates(state["payload"], config, sources)
                if not result:
                    raise RagFailure("QUIZ_INVALID", "没有有效题目")
            return {"result": result, "trace": [*state["trace"], {"tool": terminal}]}

        def route(state: EvidenceState) -> str:
            assert state["decision"] is not None
            return {"search": "search", "read_source": "read"}.get(
                state["decision"].tool, "generate"
            )

        graph = StateGraph(EvidenceState)
        graph.add_node("plan", plan)
        graph.add_node("search", search_node)
        graph.add_node("read", read_node)
        graph.add_node("generate", generate_node)
        graph.add_node("validate", validate_node)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", route, ["search", "read", "generate"])
        graph.add_edge("search", "plan")
        graph.add_edge("read", "plan")
        graph.add_edge("generate", "validate")
        graph.add_edge("validate", END)
        with tracing_context(enabled=False):
            state = await graph.compile().ainvoke(
                {
                    "observations": [],
                    "discovered": {},
                    "selected": {},
                    "queries": set(),
                    "steps": 0,
                    "searches": 0,
                    "reads": 0,
                    "decision": None,
                    "trace": [],
                    "last_usage": {},
                    "planning_prompt_version": None,
                    "payload": {},
                    "result": None,
                },
                config={"recursion_limit": 16, "callbacks": []},
            )
        usage = budget.usage(
            state["last_usage"],
            steps=state["steps"],
            searches=state["searches"],
            reads=state["reads"],
            planning_prompt_version=state["planning_prompt_version"],
            trace=state["trace"],
        )
        return state["result"], {**usage, "graph_version": GRAPH_VERSION}

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
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        async def decide(
            observations: list[dict[str, Any]],
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            return await model.plan_step(question, history=history, observations=observations)

        async def generate(items: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
            return await model.answer_study(question, items[0]["sources"], history=history)

        return await self._execute(
            decide=decide,
            terminal="answer",
            embeddings=embeddings,
            search=search,
            ensure_active=ensure_active,
            budget=budget,
            generate=generate,
            config=None,
        )

    async def quiz(
        self,
        config: dict[str, Any],
        *,
        embeddings: Embeddings,
        model: QuizPlanningModel,
        search: SourceSearch,
        ensure_active: RunCheck,
        budget: AgentBudget,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        async def decide(
            observations: list[dict[str, Any]],
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            return await model.plan_quiz_step(config, observations=observations)

        async def generate(items: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
            return await model.generate_quiz_with_usage(config, items[0]["sources"])

        return await self._execute(
            decide=decide,
            terminal="generate_quiz",
            embeddings=embeddings,
            search=search,
            ensure_active=ensure_active,
            budget=budget,
            generate=generate,
            config=config,
        )
