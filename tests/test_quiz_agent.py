from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from app.learning.domain import validate_blueprint
from app.learning.quiz_agent import run_quiz_agent
from app.rag.domain import Evidence, RagFailure


class QuizModel:
    def __init__(self, decisions: list[dict[str, Any]], payload: dict[str, Any]) -> None:
        self.decisions = iter(decisions)
        self.payload = payload
        self.generated_sources: list[Evidence] = []

    async def plan_quiz_step(
        self, config: dict[str, Any], *, observations: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return next(self.decisions), {
            "model": "fake",
            "prompt_version": "test-plan-v1",
            "prompt_tokens": 20,
            "completion_tokens": 5,
        }

    async def generate_quiz_with_usage(
        self, config: dict[str, Any], sources: list[Evidence]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.generated_sources = sources
        return self.payload, {"model": "fake", "prompt_tokens": 30, "completion_tokens": 10}


class Embeddings:
    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        return [[1.0] * 1024 for _ in texts]


def config() -> dict[str, Any]:
    return validate_blueprint(
        {
            "type_counts": {"single": 1, "multiple": 0, "true_false": 0, "short": 0},
            "difficulty": "medium",
            "language": "en",
            "topic": "statistics",
            "generation_mode": "agent",
        }
    )


def source() -> Evidence:
    return Evidence(
        uuid4(),
        "The median resists extreme outliers.",
        1,
        {"kind": "page", "position": 1},
        document_id=uuid4(),
        version_id=7,
    )


def candidate(evidence: Evidence) -> dict[str, Any]:
    return {
        "kind": "single",
        "topic": "statistics",
        "stem": "Which statistic resists outliers?",
        "options": ["Median", "Mean", "Mode"],
        "answer": "Median",
        "explanation": "The source identifies a robust statistic.",
        "citations": [{"source_id": str(evidence.id), "quote": evidence.content}],
    }


async def active() -> None:
    pass


def test_quiz_agent_publishes_only_valid_candidates_from_read_evidence() -> None:
    read, unread = source(), source()
    valid = candidate(read)
    model = QuizModel(
        [
            {"tool": "search", "query": "statistics"},
            {"tool": "read_source", "source_id": str(read.id)},
            {"tool": "generate_quiz"},
        ],
        {
            "questions": [
                candidate(unread),
                {**valid, "options": ["Median", "Median"]},
                valid,
                valid,
            ]
        },
    )

    async def scoped_search(vector: list[float], query: str) -> list[Evidence]:
        return [read, unread]

    questions, usage = asyncio.run(
        run_quiz_agent(
            config(),
            embeddings=Embeddings(),
            model=model,
            search=scoped_search,
            ensure_active=active,
        )
    )
    assert len(questions) == 1
    assert questions[0]["sources"][0]["document_id"] == str(read.document_id)
    assert model.generated_sources == [read]
    assert usage["model_calls"] == 4
    assert usage["planning_prompt_version"] == "test-plan-v1"
    assert usage["agent_version"] == "scoped-quiz-tools-v1"
    assert "statistics" not in str(usage) and read.content not in str(usage)


@pytest.mark.parametrize("terminal", ["generate_quiz", "answer"])
def test_quiz_agent_rejects_missing_evidence_or_answer_tool(terminal: str) -> None:
    model = QuizModel([{"tool": "search", "query": "missing"}, {"tool": terminal}], {})

    async def scoped_search(vector: list[float], query: str) -> list[Evidence]:
        return []

    with pytest.raises(RagFailure) as caught:
        asyncio.run(
            run_quiz_agent(
                config(),
                embeddings=Embeddings(),
                model=model,
                search=scoped_search,
                ensure_active=active,
            )
        )
    assert caught.value.code == (
        "QUIZ_NO_EVIDENCE" if terminal == "generate_quiz" else "AGENT_TOOL_INVALID"
    )
    assert model.generated_sources == []


def test_standard_blueprints_remain_identical_for_old_idempotent_requests() -> None:
    planned = config()
    original = {key: value for key, value in planned.items() if key != "generation_mode"}
    assert validate_blueprint(original) == validate_blueprint(
        {**original, "generation_mode": "standard"}
    )
    with pytest.raises(ValueError, match="generation mode"):
        validate_blueprint({**original, "generation_mode": "unbounded"})
