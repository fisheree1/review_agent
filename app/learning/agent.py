"""Model-call budgets and ports shared by scoped study generation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.rag.domain import (
    Evidence,
    RagFailure,
)

AGENT_VERSION = "scoped-study-tools-v1"
MAX_MODEL_CALLS = 7
MAX_BILLABLE_TOKENS = 18_000
# Output tokens carry a higher relative cost. This is not a USD estimate.
MAX_COST_UNITS = 30_000


class StudyGenerationModel(Protocol):
    async def answer_study(
        self,
        question: str,
        sources: list[Evidence],
        *,
        history: list[dict[str, str]] | None = None,
        mode: Literal["focused", "overview"] = "focused",
        repair_feedback: dict[str, Any] | None = None,
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
