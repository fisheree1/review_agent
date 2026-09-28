"""Opt-in paid planning, cited summary, Quiz and grading on synthetic scoped material.

This checks provider contracts, not PostgreSQL retrieval or semantic entailment.
Only content-free metrics are printed. Durable user journeys have separate DB tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.core.config import ModelSettings
from app.learning.agent import AgentBudget
from app.learning.conversation_tasks import validate_task_plan
from app.learning.infrastructure.langgraph_workflow import LangGraphStudyExecutor
from app.learning.workflow import check_run_budget
from app.rag.domain import Evidence
from app.rag.providers import CloudModels
from scripts.evaluate_agent import EVAL_PATH, Fixture


async def evaluate() -> dict[str, Any]:
    fixture = Fixture.model_validate_json(EVAL_PATH.read_text())
    sources = [
        Evidence(
            uuid5(NAMESPACE_URL, d.id),
            d.text,
            1,
            {"kind": "page", "position": 1},
            document_id=uuid5(NAMESPACE_URL, f"document:{d.id}"),
            version_id=1,
        )
        for d in fixture.documents
        if d.workspace == "study" and d.id in {"median", "mean"}
    ]
    usages: list[dict[str, Any]] = []

    async def active() -> None:
        pass

    async def search(vector: list[float], query: str) -> list[Evidence]:
        return sources

    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client:
        models = CloudModels(ModelSettings(), client)
        request = (
            "Summarize mean and median using the selected documents, then create exactly "
            "one single-choice and one short-answer question in English, medium difficulty. "
            "After I submit, review mistakes and give weak-topic practice."
        )
        payload, usage = await models.plan_task(request, history=[], review_available=False)
        usages.append(usage)
        plan = validate_task_plan(payload)
        assert plan.action == "study" and plan.review_after_submit and plan.practice_after_review
        config = plan.quiz_config()
        assert config["type_counts"] == {"single": 1, "multiple": 0, "true_false": 0, "short": 1}
        graph = LangGraphStudyExecutor()
        summary, usage = await graph.answer(
            plan.summary_request or request,
            [],
            model=models,
            embeddings=models,
            search=search,
            ensure_active=active,
            budget=AgentBudget(),
        )
        usages.append(usage)
        assert not summary["insufficient_evidence"] and summary["claims"]
        questions, usage = await graph.quiz(
            config,
            model=models,
            embeddings=models,
            search=search,
            ensure_active=active,
            budget=AgentBudget(),
        )
        usages.append(usage)
        assert len(questions) == 2 and {q["kind"] for q in questions} == {"single", "short"}
        short = next(q for q in questions if q["kind"] == "short")
        question_id = str(uuid5(NAMESPACE_URL, "synthetic-short-answer"))
        grades, usage = await models.grade_short_with_usage(
            [
                {
                    "question_id": question_id,
                    "stem": short["stem"],
                    "reference": short["answer"],
                    "response": "I do not know.",
                    "sources": short["sources"],
                }
            ]
        )
        usages.append(usage)
        assert len(grades) == 1 and grades[0]["question_id"] == question_id
        assert grades[0]["score"] < 0.7
        weak_config = plan.quiz_config([short["topic"]])
        weak_config["topic"] = short["topic"][:120]
        weak, usage = await graph.quiz(
            weak_config,
            model=models,
            embeddings=models,
            search=search,
            ensure_active=active,
            budget=AgentBudget(),
        )
        usages.append(usage)
        assert weak
    totals = {
        "model_calls": sum(u.get("model_calls", 1) for u in usages),
        "prompt_tokens": sum(u["prompt_tokens"] for u in usages),
        "completion_tokens": sum(u["completion_tokens"] for u in usages),
        "cost_units": sum(u["prompt_tokens"] + 4 * u["completion_tokens"] for u in usages),
    }
    check_run_budget(totals)
    return {
        "version": "study-provider-contract-v1",
        "passed": True,
        "validated_questions": len(questions),
        "weak_questions": len(weak),
        **totals,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        Fixture.model_validate_json(EVAL_PATH.read_text())
        print("Validated synthetic study fixture; no model calls.")
        return
    print(json.dumps(asyncio.run(evaluate())))


if __name__ == "__main__":
    main()
