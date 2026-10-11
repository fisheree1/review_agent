"""Validate study-plan intent fixtures, or explicitly check the paid planner with --live.

Each case fixes a current request and the plan steps it must produce, including follow-up
steps that run only after the user submits a Quiz. Output contains case IDs, prompt version,
step labels and pass/fail checks, never request text or model payloads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import ModelSettings
from app.learning.graph_tasks import GraphTaskPlan, Step, validate_graph_task_plan
from app.rag.domain import RagFailure
from app.rag.providers import CloudModels

EVAL_PATH = Path(__file__).resolve().parent.parent / "evals" / "study-plan-v1.json"
STEP_ORDER: list[Step] = ["summary", "pdf", "quiz", "review", "practice"]


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    category: str
    request: str = Field(min_length=2, max_length=2000)
    review_available: bool = False
    expected_steps: list[Step] = Field(min_length=1, max_length=5)
    type_counts: dict[str, int] | None = None
    language: Literal["zh", "en", "zh-en"] | None = None

    @model_validator(mode="after")
    def validate_expectation(self) -> Case:
        if self.expected_steps != sorted(set(self.expected_steps), key=STEP_ORDER.index):
            raise ValueError("Expected steps must be unique and ordered")
        writes_quiz = bool({"quiz", "practice"} & set(self.expected_steps))
        if (self.type_counts is not None or self.language is not None) and not writes_quiz:
            raise ValueError("Quiz expectations require a quiz step")
        return self


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    cases: list[Case] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_ids(self) -> Fixture:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Evaluation IDs must be unique")
        return self


def score_plan(case: Case, plan: GraphTaskPlan) -> dict[str, bool]:
    """Compare a validated plan with the case. Model wording is never scored."""
    checks = {"steps_match": plan.steps == case.expected_steps}
    if case.type_counts is not None:
        checks["type_counts_match"] = (
            plan.config is not None and plan.config.type_counts == case.type_counts
        )
    if case.language is not None:
        checks["language_match"] = plan.config is not None and plan.config.language == case.language
    return checks


async def evaluate(fixture: Fixture) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client:
        models = CloudModels(ModelSettings(), client)
        for case in fixture.cases:
            row: dict[str, Any] = {"id": case.id, "category": case.category}
            try:
                payload, usage = await models.plan_graph_task(
                    case.request,
                    history=[],
                    review_available=case.review_available,
                    memory_summary="",
                )
                plan = validate_graph_task_plan(payload)
            except RagFailure as exc:
                row.update(passed=False, error=exc.code)
            else:
                checks = score_plan(case, plan)
                row.update(
                    passed=all(checks.values()),
                    checks=checks,
                    steps=plan.steps,
                    expected_steps=case.expected_steps,
                    prompt_version=usage.get("prompt_version"),
                )
            results.append(row)
    return {"version": fixture.version, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call the paid planning model")
    parser.add_argument("--output", type=Path, help="Write a content-free report")
    args = parser.parse_args()
    fixture = Fixture.model_validate_json(EVAL_PATH.read_text(encoding="utf-8"))
    if not args.live:
        print(f"Validated {fixture.version}: {len(fixture.cases)} cases; no model calls.")
        return 0
    report = asyncio.run(evaluate(fixture))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in report["results"]:
        print(json.dumps(row, ensure_ascii=False))
    return 0 if all(row["passed"] for row in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
