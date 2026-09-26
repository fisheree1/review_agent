from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from app.learning.agent import MAX_TOOL_STEPS, run_scoped_agent
from app.rag.domain import Evidence, RagFailure

VECTOR = [1.0] * 1024


class FakeEmbeddings:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        assert query and len(texts) == 1
        self.queries.extend(texts)
        return [VECTOR]


class PlannedModel:
    def __init__(self, decisions: list[dict[str, Any]], *, prompt_tokens: int = 20) -> None:
        self.decisions = iter(decisions)
        self.prompt_tokens = prompt_tokens
        self.answer_sources: list[Evidence] = []
        self.observations: list[list[dict[str, Any]]] = []

    async def plan_step(
        self,
        question: str,
        *,
        history: list[dict[str, str]],
        observations: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.observations.append(list(observations))
        return next(self.decisions), {
            "model": "fake",
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": 5,
        }

    async def answer(
        self, question: str, sources: list[Evidence], *, history: list[dict[str, str]] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.answer_sources = sources
        return {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "The median resists outliers.",
                    "citations": [
                        {"source_id": str(sources[0].id), "quote": sources[0].content[:60]}
                    ],
                }
            ],
        }, {"model": "fake", "prompt_tokens": 30, "completion_tokens": 10}


def source(content: str) -> Evidence:
    return Evidence(
        uuid4(), content, 1, {"kind": "page", "position": 1}, document_id=uuid4(), version_id=7
    )


def test_agent_selects_multiple_scoped_tools_before_publishing_cited_answer() -> None:
    first = source("The median resists extreme outliers. " * 8)
    second = source("Variation describes the spread of measurements. " * 5)
    model = PlannedModel(
        [
            {"tool": "search", "query": "median"},
            {"tool": "read_source", "source_id": str(first.id)},
            {"tool": "search", "query": "variation"},
            {"tool": "read_source", "source_id": str(second.id)},
            {"tool": "answer"},
        ]
    )
    embeddings = FakeEmbeddings()

    async def scoped_search(vector: list[float], query: str) -> list[Evidence]:
        assert vector == VECTOR
        return [first] if query == "median" else [second]

    answer, usage = asyncio.run(
        run_scoped_agent(
            "Compare these ideas",
            [],
            embeddings=embeddings,
            model=model,
            search=scoped_search,
        )
    )
    assert embeddings.queries == ["median", "variation"]
    assert model.answer_sources == [first, second]
    assert answer["claims"][0]["citations"][0]["document_id"] == str(first.document_id)
    assert usage["tool_steps"] == MAX_TOOL_STEPS
    assert usage["model_calls"] == 6
    assert usage["search_calls"] == 2 and usage["read_calls"] == 2
    assert len(model.observations[1][0]["sources"][0]["preview"]) == 180


def test_agent_refuses_without_evidence_and_never_calls_generation() -> None:
    model = PlannedModel([{"tool": "search", "query": "unknown fact"}, {"tool": "answer"}])

    async def no_results(vector: list[float], query: str) -> list[Evidence]:
        return []

    answer, usage = asyncio.run(
        run_scoped_agent(
            "Unknown fact?", [], embeddings=FakeEmbeddings(), model=model, search=no_results
        )
    )
    assert answer == {"insufficient_evidence": True, "claims": []}
    assert model.answer_sources == []
    assert usage["model_calls"] == 2


@pytest.mark.parametrize(
    ("bad_decision", "expected_code"),
    [
        ({"tool": "read_source", "source_id": str(uuid4())}, "AGENT_TOOL_INVALID"),
        ({"tool": "delete", "source_id": str(uuid4())}, "AGENT_PLAN_INVALID"),
    ],
)
def test_agent_rejects_unseen_sources_and_unlisted_tools(
    bad_decision: dict[str, Any], expected_code: str
) -> None:
    first = source("The median resists extreme outliers.")
    model = PlannedModel([{"tool": "search", "query": "median"}, bad_decision])

    async def scoped_search(vector: list[float], query: str) -> list[Evidence]:
        return [first]

    with pytest.raises(RagFailure) as caught:
        asyncio.run(
            run_scoped_agent(
                "Find evidence", [], embeddings=FakeEmbeddings(), model=model, search=scoped_search
            )
        )
    assert caught.value.code == expected_code
    assert model.answer_sources == []


def test_agent_stops_before_tool_execution_when_cost_budget_is_exceeded() -> None:
    model = PlannedModel([{"tool": "search", "query": "median"}], prompt_tokens=40_000)
    embeddings = FakeEmbeddings()

    async def should_not_search(vector: list[float], query: str) -> list[Evidence]:
        raise AssertionError("Over-budget plan must not execute a tool")

    with pytest.raises(RagFailure) as caught:
        asyncio.run(
            run_scoped_agent(
                "Find evidence", [], embeddings=embeddings, model=model, search=should_not_search
            )
        )
    assert caught.value.code == "AGENT_BUDGET_EXCEEDED"
    assert embeddings.queries == []


def test_agent_stops_repeated_search_instead_of_looping() -> None:
    model = PlannedModel(
        [{"tool": "search", "query": "median"}, {"tool": "search", "query": "MEDIAN"}]
    )
    calls = 0

    async def scoped_search(vector: list[float], query: str) -> list[Evidence]:
        nonlocal calls
        calls += 1
        return []

    with pytest.raises(RagFailure) as caught:
        asyncio.run(
            run_scoped_agent(
                "Find evidence", [], embeddings=FakeEmbeddings(), model=model, search=scoped_search
            )
        )
    assert caught.value.code == "AGENT_TOOL_INVALID"
    assert calls == 1


def test_agent_never_publishes_a_citation_from_an_unread_search_result() -> None:
    first = source("The median resists extreme outliers.")
    unread = source("The mean changes when an outlier is added.")

    class MisattributingModel(PlannedModel):
        async def answer(
            self,
            question: str,
            sources: list[Evidence],
            *,
            history: list[dict[str, str]] | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            payload, usage = await super().answer(question, sources, history=history)
            payload["claims"][0]["citations"][0]["source_id"] = str(unread.id)
            return payload, usage

    model = MisattributingModel(
        [
            {"tool": "search", "query": "statistics"},
            {"tool": "read_source", "source_id": str(first.id)},
            {"tool": "answer"},
        ]
    )

    async def scoped_search(vector: list[float], query: str) -> list[Evidence]:
        return [first, unread]

    with pytest.raises(RagFailure) as caught:
        asyncio.run(
            run_scoped_agent(
                "Compare statistics",
                [],
                embeddings=FakeEmbeddings(),
                model=model,
                search=scoped_search,
            )
        )
    assert caught.value.code == "ANSWER_INVALID"


def test_agent_step_limit_ends_an_unfinished_plan_without_generation() -> None:
    first = source("The median resists extreme outliers.")
    second = source("Variation describes a distribution's spread.")
    third = source("Variance is one measure of variation.")
    model = PlannedModel(
        [
            {"tool": "search", "query": "median"},
            {"tool": "read_source", "source_id": str(first.id)},
            {"tool": "search", "query": "variation"},
            {"tool": "read_source", "source_id": str(second.id)},
            {"tool": "read_source", "source_id": str(third.id)},
        ]
    )

    async def scoped_search(vector: list[float], query: str) -> list[Evidence]:
        return [first] if query == "median" else [second, third]

    with pytest.raises(RagFailure) as caught:
        asyncio.run(
            run_scoped_agent(
                "Compare statistics",
                [],
                embeddings=FakeEmbeddings(),
                model=model,
                search=scoped_search,
            )
        )
    assert caught.value.code == "AGENT_STEP_LIMIT"
    assert model.answer_sources == []


def test_cancelled_agent_stops_before_the_next_paid_tool() -> None:
    model = PlannedModel([{"tool": "search", "query": "median"}])
    embeddings = FakeEmbeddings()
    checks = 0

    async def ensure_active() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RagFailure("AGENT_RUN_INACTIVE", "cancelled")

    async def should_not_search(vector: list[float], query: str) -> list[Evidence]:
        raise AssertionError("Cancellation must stop before embedding and retrieval")

    with pytest.raises(RagFailure, match="cancelled"):
        asyncio.run(
            run_scoped_agent(
                "Find evidence",
                [],
                embeddings=embeddings,
                model=model,
                search=should_not_search,
                ensure_active=ensure_active,
            )
        )
    assert embeddings.queries == []
    assert len(model.observations) == 1


def test_question_agent_cannot_invoke_quiz_generation() -> None:
    model = PlannedModel([{"tool": "generate_quiz"}])

    async def should_not_search(vector: list[float], query: str) -> list[Evidence]:
        raise AssertionError("Forbidden tools must not run")

    with pytest.raises(RagFailure) as caught:
        asyncio.run(
            run_scoped_agent(
                "Find evidence",
                [],
                embeddings=FakeEmbeddings(),
                model=model,
                search=should_not_search,
            )
        )
    assert caught.value.code == "AGENT_TOOL_INVALID"
