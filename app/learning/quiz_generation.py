"""One scoped evidence selection and one generation for every Quiz path.

Evidence is chosen by application code (topic retrieval or whole-scope sampling), never by
model-driven tool calls, so a Quiz costs one generation call plus at most one embedding.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.learning.agent import AgentBudget, RunCheck
from app.learning.domain import validate_candidates
from app.rag.domain import Evidence, RagFailure, validate_vectors
from app.rag.ports import Embeddings

QUIZ_GENERATION_VERSION = "scoped-quiz-evidence-v1"
QuizSources = Callable[[list[float] | None, str], Awaitable[list[Evidence]]]


class QuizGenerationModel(Protocol):
    async def generate_quiz_with_usage(
        self, config: dict[str, Any], sources: list[Evidence]
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...


async def generate_scoped_quiz(
    config: dict[str, Any],
    *,
    embeddings: Embeddings,
    model: QuizGenerationModel,
    sources: QuizSources,
    ensure_active: RunCheck,
    budget: AgentBudget | None = None,
    charge_usage: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """``charge_usage=False`` keeps standard Quizzes working when a provider omits usage."""
    budget = budget if budget is not None else AgentBudget()
    topic = str(config.get("topic", "")).strip()
    vector: list[float] | None = None
    await ensure_active()
    if topic:
        vectors = await embeddings.embed([topic], query=True)
        validate_vectors(vectors, 1)
        vector = vectors[0]
        await ensure_active()
    evidence = await sources(vector, topic)
    if not evidence:
        raise RagFailure("QUIZ_NO_EVIDENCE", "没有可用出题证据")
    await ensure_active()
    payload, usage = await model.generate_quiz_with_usage(config, evidence)
    if charge_usage:
        budget.charge(usage)
    questions = validate_candidates(payload, config, evidence)
    if not questions:
        raise RagFailure("QUIZ_INVALID", "没有有效题目")
    return questions, {
        **usage,
        "quiz_strategy": QUIZ_GENERATION_VERSION,
        "evidence_selection": "topic" if topic else "spread",
        "evidence_count": len(evidence),
        "accepted_questions": len(questions),
        "search_calls": 1 if topic else 0,
        "model_calls": budget.model_calls,
        "prompt_tokens": budget.prompt_tokens,
        "completion_tokens": budget.completion_tokens,
        "cost_units": budget.cost_units,
    }
