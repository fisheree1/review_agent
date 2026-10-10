"""Bounded, composable plans for the durable study workflow."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.learning.conversation_tasks import QuizTaskConfig
from app.learning.domain import validate_blueprint
from app.rag.domain import RagFailure

GRAPH_TASK_VERSION = "study-intent-plan-v1"
Step = Literal["summary", "pdf", "quiz", "review", "practice"]


class GraphTaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    steps: list[Step] = Field(default_factory=list, max_length=5)
    summary_request: str | None = Field(default=None, min_length=2, max_length=1200)
    summary_mode: Literal["focused", "overview"] = "focused"
    title: str | None = Field(default=None, min_length=1, max_length=160)
    config: QuizTaskConfig | None = None
    clarification: str | None = Field(default=None, min_length=2, max_length=600)
    # A conversation hint only. It never serves as source evidence or authorizes a write.
    memory_summary: str = Field(default="", max_length=700)

    @model_validator(mode="after")
    def validate_steps(self) -> Self:
        order = ["summary", "pdf", "quiz", "review", "practice"]
        if self.clarification is not None:
            if self.steps or self.config is not None or self.title is not None:
                raise ValueError("Clarification cannot execute steps")
            return self
        if not self.steps or self.steps != sorted(set(self.steps), key=order.index):
            raise ValueError("Steps must be unique and ordered")
        if "pdf" in self.steps and "summary" not in self.steps:
            raise ValueError("PDF export requires a cited summary")
        if "practice" in self.steps and "review" not in self.steps:
            raise ValueError("Weak-topic practice requires actual review")
        if "quiz" in self.steps or "practice" in self.steps:
            if self.config is None or self.title is None or not self.title.strip():
                raise ValueError("Quiz steps require a title and blueprint")
            self.title = self.title.strip()
        elif self.config is not None or self.title is not None:
            raise ValueError("Only quiz steps accept a blueprint and title")
        if "summary" not in self.steps and self.summary_request is not None:
            raise ValueError("Summary request requires a summary step")
        return self

    def quiz_config(self, weak_topics: list[str] | None = None) -> dict[str, Any]:
        if self.config is None:
            raise RagFailure("TASK_PLAN_INVALID", "缺少出题设置")
        config = validate_blueprint({**self.config.model_dump(), "generation_mode": "agent"})
        if weak_topics is not None:
            if not weak_topics:
                raise RagFailure("REVIEW_NOT_AVAILABLE", "当前资料范围没有可用薄弱点")
            config["topic"] = "、".join(weak_topics)[:120]
        return config


def validate_graph_task_plan(payload: dict[str, Any]) -> GraphTaskPlan:
    try:
        return GraphTaskPlan.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise RagFailure("TASK_PLAN_INVALID", "未能识别有效任务，请明确说明要求") from exc
