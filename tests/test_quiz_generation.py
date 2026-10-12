"""Every Quiz path selects evidence in application code and calls the model once."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from app.learning.domain import validate_blueprint
from app.learning.quiz_generation import generate_scoped_quiz
from app.learning.scope import spread_positions
from app.rag.domain import Evidence, RagFailure


class QuizModel:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[list[Evidence]] = []

    async def generate_quiz_with_usage(
        self, config: dict[str, Any], sources: list[Evidence]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.calls.append(sources)
        return self.payload, {"model": "fake", "prompt_tokens": 30, "completion_tokens": 10}


class Embeddings:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        self.texts.extend(texts)
        return [[1.0] * 1024 for _ in texts]


class Sources:
    def __init__(self, evidence: list[Evidence]) -> None:
        self.evidence = evidence
        self.requests: list[tuple[list[float] | None, str]] = []

    async def __call__(self, vector: list[float] | None, topic: str) -> list[Evidence]:
        self.requests.append((vector, topic))
        return self.evidence


def config(topic: str = "statistics") -> dict[str, Any]:
    return validate_blueprint(
        {
            "type_counts": {"single": 1, "multiple": 0, "true_false": 0, "short": 0},
            "difficulty": "medium",
            "language": "en",
            "topic": topic,
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


def run(
    quiz_config: dict[str, Any],
    model: QuizModel,
    sources: Sources,
    embeddings: Embeddings | None = None,
    ensure_active: Any = active,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return asyncio.run(
        generate_scoped_quiz(
            quiz_config,
            embeddings=embeddings or Embeddings(),
            model=model,
            sources=sources,
            ensure_active=ensure_active,
        )
    )


def test_publishes_only_valid_candidates_with_one_generation_call() -> None:
    evidence = source()
    invented = {**candidate(evidence), "stem": "Which is invented?"}
    invented["citations"] = [{"source_id": str(evidence.id), "quote": "not in the source"}]
    model = QuizModel({"questions": [candidate(evidence), invented]})
    questions, usage = run(config(), model, Sources([evidence]))
    assert [question["stem"] for question in questions] == ["Which statistic resists outliers?"]
    assert len(model.calls) == 1 and model.calls[0] == [evidence]
    assert usage["model_calls"] == 1 and usage["accepted_questions"] == 1


def test_topic_quiz_retrieves_by_topic_and_whole_scope_quiz_samples_without_embedding() -> None:
    evidence = source()
    embeddings, sources = Embeddings(), Sources([evidence])
    _, usage = run(
        config("median"), QuizModel({"questions": [candidate(evidence)]}), sources, embeddings
    )
    assert embeddings.texts == ["median"] and sources.requests[0][1] == "median"
    assert sources.requests[0][0] is not None and usage["evidence_selection"] == "topic"

    embeddings, sources = Embeddings(), Sources([evidence])
    _, usage = run(config(""), QuizModel({"questions": [candidate(evidence)]}), sources, embeddings)
    assert embeddings.texts == [] and sources.requests == [(None, "")]
    assert usage["evidence_selection"] == "spread" and usage["search_calls"] == 0


def test_missing_evidence_fails_before_any_generation_call() -> None:
    model = QuizModel({"questions": []})
    with pytest.raises(RagFailure) as failure:
        run(config(), model, Sources([]))
    assert failure.value.code == "QUIZ_NO_EVIDENCE" and model.calls == []


def test_quiz_without_any_valid_question_is_not_published() -> None:
    evidence = source()
    broken = {**candidate(evidence), "answer": "Not an option"}
    with pytest.raises(RagFailure) as failure:
        run(config(), QuizModel({"questions": [broken]}), Sources([evidence]))
    assert failure.value.code == "QUIZ_INVALID"


def test_cancellation_stops_before_the_paid_generation_call() -> None:
    checks = 0

    async def cancel_after_retrieval() -> None:
        nonlocal checks
        checks += 1
        if checks >= 3:
            raise RagFailure("AGENT_RUN_INACTIVE", "cancelled")

    model = QuizModel({"questions": [candidate(source())]})
    with pytest.raises(RagFailure, match="cancelled"):
        run(config(), model, Sources([source()]), ensure_active=cancel_after_retrieval)
    assert model.calls == []


def test_standard_blueprints_remain_identical_for_old_idempotent_requests() -> None:
    planned = config()
    original = {key: value for key, value in planned.items() if key != "generation_mode"}
    assert validate_blueprint(original) == validate_blueprint(
        {**original, "generation_mode": "standard"}
    )
    with pytest.raises(ValueError, match="generation mode"):
        validate_blueprint({**original, "generation_mode": "unbounded"})


@pytest.mark.parametrize(("total", "count"), [(300, 12), (13, 12), (12, 12), (5, 12), (1, 1)])
def test_whole_scope_sampling_spans_the_document_instead_of_its_opening(
    total: int, count: int
) -> None:
    positions = spread_positions(total, count)
    assert positions == sorted(set(positions)) and all(0 <= p < total for p in positions)
    assert len(positions) == min(total, count)
    if total > count:
        # Previously only the first ``count`` chunks were used.
        assert positions[-1] >= total - total // count


def test_sampling_without_chunks_or_quota_selects_nothing() -> None:
    assert spread_positions(0, 12) == [] and spread_positions(40, 0) == []
