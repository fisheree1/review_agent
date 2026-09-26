"""Typed user task plans; the application owns authorization and publication."""

from __future__ import annotations

from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.learning.domain import validate_blueprint
from app.rag.domain import RagFailure

TASK_VERSION = "conversation-tasks-v1"


class QuizTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type_counts: dict[str, int]
    difficulty: Literal["easy", "medium", "hard"]
    language: Literal["zh", "en"]
    topic: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        validate_blueprint(self.model_dump())
        return self


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["answer", "create_quiz", "review_mistakes", "practice_weak_topics", "clarify"]
    title: str | None = Field(default=None, min_length=1, max_length=160)
    config: QuizTaskConfig | None = None
    message: str | None = Field(default=None, min_length=2, max_length=600)

    @model_validator(mode="after")
    def validate_arguments(self) -> Self:
        if self.action in ("create_quiz", "practice_weak_topics"):
            if (
                self.title is None
                or not self.title.strip()
                or self.config is None
                or self.message is not None
            ):
                raise ValueError("Quiz tasks require only a title and blueprint")
            self.title = self.title.strip()
        elif self.action == "clarify":
            if (
                self.message is None
                or not self.message.strip()
                or self.config is not None
                or self.title is not None
            ):
                raise ValueError("Clarification requires only a message")
            self.message = self.message.strip()
        elif self.config is not None or self.title is not None or self.message is not None:
            raise ValueError("Read-only tasks have no additional arguments")
        return self

    def quiz_config(self, weak_topics: list[str] | None = None) -> dict[str, Any]:
        if self.config is None:
            raise RagFailure("TASK_PLAN_INVALID", "缺少出题设置")
        config = validate_blueprint({**self.config.model_dump(), "generation_mode": "agent"})
        if self.action == "practice_weak_topics":
            if not weak_topics:
                raise RagFailure("REVIEW_NOT_AVAILABLE", "当前资料范围没有可用薄弱点")
            config["topic"] = "、".join(weak_topics)[:120]
        return config


class TaskPlanningModel(Protocol):
    async def plan_task(
        self, request: str, *, history: list[dict[str, str]], review_available: bool
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...


def validate_task_plan(payload: dict[str, Any]) -> TaskPlan:
    try:
        return TaskPlan.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise RagFailure("TASK_PLAN_INVALID", "未能识别有效任务，请明确说明要求") from exc
