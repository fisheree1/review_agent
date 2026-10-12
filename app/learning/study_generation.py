"""Bounded study generation with exact-source formatting recovery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, Literal

from app.learning.agent import (
    MAX_BILLABLE_TOKENS,
    MAX_COST_UNITS,
    AgentBudget,
    RunCheck,
    StudyPlanningModel,
)
from app.rag.domain import (
    AnswerValidationFailure,
    Evidence,
    RagFailure,
    canonical_excerpt,
    validate_study_answer,
)

STUDY_GENERATION_VERSION = "validated-study-generation-v1"
ValidationRecorder = Callable[[int, dict[str, Any]], Awaitable[None]]


def resolve_quote_whitespace(
    payload: dict[str, Any], sources: list[Evidence]
) -> tuple[dict[str, Any], int]:
    corrected = deepcopy(payload)
    available = {str(source.id): source.content for source in sources}
    fixed = 0
    claims = corrected.get("claims")
    if not isinstance(claims, list):
        return corrected, fixed
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("citations"), list):
            continue
        for citation in claim["citations"]:
            if not isinstance(citation, dict):
                continue
            quote = citation.get("quote")
            content = available.get(str(citation.get("source_id")))
            if isinstance(quote, str) and content is not None:
                original = canonical_excerpt(quote, content)
                if original is not None and original != quote:
                    citation["quote"] = original
                    fixed += 1
    return corrected, fixed


async def generate_study(
    question: str,
    sources: list[Evidence],
    history: list[dict[str, str]],
    *,
    model: StudyPlanningModel,
    budget: AgentBudget,
    ensure_active: RunCheck,
    mode: Literal["focused", "overview"],
    record_validation: ValidationRecorder | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    feedback: dict[str, Any] | None = None
    diagnoses: list[dict[str, Any]] = []
    whitespace_fixes = 0
    for attempt in range(2):
        await ensure_active()
        raw, usage = await model.answer_study(
            question, sources, history=history, mode=mode, repair_feedback=feedback
        )
        budget.charge(usage)
        await ensure_active()
        raw, fixed = resolve_quote_whitespace(raw, sources)
        whitespace_fixes += fixed
        try:
            answer = validate_study_answer(raw, sources, require_sections=True)
            if mode == "focused" and len(answer["claims"]) > 4:
                raise AnswerValidationFailure("focused_point_limit", length=len(answer["claims"]))
        except AnswerValidationFailure as exc:
            feedback = exc.diagnosis()
            diagnoses.append({"attempt": attempt, **feedback})
            if record_validation is not None:
                await record_validation(attempt, feedback)
            if attempt == 1:
                raise
            # The retry repeats the same evidence plus a small feedback object.
            # Reserve its output cap before initiating another billed operation.
            reserved_input = usage["prompt_tokens"] + 256
            reserved_output = 2000 if mode == "focused" else 3000
            if (
                budget.prompt_tokens + budget.completion_tokens + reserved_input + reserved_output
                > MAX_BILLABLE_TOKENS
                or budget.cost_units + reserved_input + 4 * reserved_output > MAX_COST_UNITS
            ):
                raise RagFailure("AGENT_BUDGET_EXCEEDED", "剩余预算不足以修复回答") from exc
            # Only a received, charged, invalid answer is retried. Transport
            # errors and unknown requests escape; recovery uses call receipts.
            continue
        return answer, {
            **usage,
            "generation_strategy": STUDY_GENERATION_VERSION,
            "model_calls": budget.model_calls,
            "prompt_tokens": budget.prompt_tokens,
            "completion_tokens": budget.completion_tokens,
            "cost_units": budget.cost_units,
            "repair_calls": attempt,
            "quote_whitespace_fixes": whitespace_fixes,
            "answer_validation": diagnoses,
        }
    raise AssertionError("Bounded generation returned without a result")
