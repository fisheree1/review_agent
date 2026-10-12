"""One scoped retrieval and one generation for focused study explanations."""

from typing import Any

from app.learning.agent import AgentBudget, RunCheck, SourceSearch, StudyPlanningModel
from app.learning.study_generation import ValidationRecorder, generate_study
from app.rag.domain import STUDY_PROMPT_VERSION, validate_study_answer, validate_vectors
from app.rag.ports import Embeddings


async def answer_focused(
    question: str,
    history: list[dict[str, str]],
    *,
    embeddings: Embeddings,
    model: StudyPlanningModel,
    search: SourceSearch,
    ensure_active: RunCheck,
    reliable: bool = False,
    record_validation: ValidationRecorder | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    # Preserve the last same-scope subject when a plan leaves a follow-up implicit.
    query = " ".join([history[-1]["question"], question])[:3000] if history else question
    await ensure_active()
    vectors = await embeddings.embed([query], query=True)
    validate_vectors(vectors, 1)
    await ensure_active()
    sources = (await search(vectors[0], query))[:8]
    budget = AgentBudget()
    usage: dict[str, Any] = {}
    if sources and reliable:
        result, usage = await generate_study(
            question,
            sources,
            history,
            model=model,
            budget=budget,
            ensure_active=ensure_active,
            mode="focused",
            record_validation=record_validation,
        )
    elif sources:
        await ensure_active()
        raw, usage = await model.answer_study(question, sources, history=history)
        budget.charge(usage)
        result = validate_study_answer(
            raw, sources, require_sections=usage.get("prompt_version") == STUDY_PROMPT_VERSION
        )
    else:
        result = {"insufficient_evidence": True, "claims": []}
    await ensure_active()
    return result, {
        **usage,
        "answer_strategy": "focused-retrieval-v1",
        "model_calls": budget.model_calls,
        "search_calls": 1,
        "read_calls": 0,
        "cost_units": budget.cost_units,
        "prompt_tokens": budget.prompt_tokens,
        "completion_tokens": budget.completion_tokens,
    }
