"""Opt-in scoped Quiz planning; deterministic rules own candidate publication."""

from __future__ import annotations

from typing import Any, Protocol

from app.learning.agent import AgentBudget, RunCheck, SourceSearch, plan_evidence
from app.learning.domain import validate_candidates
from app.rag.domain import Evidence, RagFailure
from app.rag.ports import Embeddings

QUIZ_AGENT_VERSION = "scoped-quiz-tools-v1"


class QuizPlanningModel(Protocol):
    async def plan_quiz_step(
        self, config: dict[str, Any], *, observations: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...

    async def generate_quiz_with_usage(
        self, config: dict[str, Any], sources: list[Evidence]
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...


async def run_quiz_agent(
    config: dict[str, Any],
    *,
    embeddings: Embeddings,
    model: QuizPlanningModel,
    search: SourceSearch,
    ensure_active: RunCheck,
    budget: AgentBudget | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    async def decide(observations: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        return await model.plan_quiz_step(config, observations=observations)

    plan = await plan_evidence(
        decide,
        "generate_quiz",
        embeddings=embeddings,
        search=search,
        ensure_active=ensure_active,
        budget=budget,
    )
    if not plan.sources:
        raise RagFailure("QUIZ_NO_EVIDENCE", "没有可用出题证据")
    await ensure_active()
    payload, usage = await model.generate_quiz_with_usage(config, plan.sources)
    plan.budget.charge(usage)
    questions = validate_candidates(payload, config, plan.sources)
    if not questions:
        raise RagFailure("QUIZ_INVALID", "没有有效题目")
    plan.trace.append({"tool": "generate_quiz", "accepted_questions": len(questions)})
    return questions, plan.usage(usage, QUIZ_AGENT_VERSION)
