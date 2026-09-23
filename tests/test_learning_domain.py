from __future__ import annotations

from uuid import uuid4

import pytest

from app.learning.domain import score_objective, validate_blueprint, validate_candidates
from app.rag.domain import Evidence


def blueprint() -> dict[str, object]:
    return validate_blueprint(
        {
            "type_counts": {"single": 1, "multiple": 1, "true_false": 1, "short": 1},
            "difficulty": "medium",
            "language": "en",
            "topic": "statistics",
        }
    )


def evidence() -> Evidence:
    return Evidence(
        uuid4(),
        "The median resists extreme outliers in this example.",
        1,
        {"kind": "page", "position": 1, "title": None, "path": []},
        document_id=uuid4(),
        version_id=7,
    )


def candidate(source: Evidence) -> dict[str, object]:
    return {
        "kind": "single",
        "topic": "statistics",
        "stem": "Which statistic resists outliers?",
        "options": ["Median", "Mean", "Mode"],
        "answer": "Median",
        "explanation": "The source names the robust statistic.",
        "citations": [{"source_id": str(source.id), "quote": source.content[:42]}],
    }


def test_quiz_rejects_unsupported_sources_duplicate_options_and_answer_leaks() -> None:
    source = evidence()
    valid = candidate(source)
    fabricated = {**valid, "citations": [{"source_id": str(uuid4()), "quote": source.content[:42]}]}
    duplicate = {**valid, "options": ["Median", "Median", "Mode"]}
    leaked = {**valid, "stem": "Why is Median the answer to this question?"}
    accepted = validate_candidates(
        {"questions": [fabricated, duplicate, leaked, valid]}, blueprint(), [source]
    )
    assert len(accepted) == 1
    assert accepted[0]["sources"][0]["document_id"] == str(source.document_id)
    assert accepted[0]["sources"][0]["version_id"] == 7


def test_quiz_rejects_invalid_requested_shape_and_scores_exact_objective_answers() -> None:
    with pytest.raises(ValueError):
        validate_blueprint(
            {
                "type_counts": {"single": 11, "multiple": 0, "true_false": 0, "short": 0},
                "difficulty": "medium",
                "language": "en",
            }
        )
    assert score_objective("single", "Median", "Median") == 1.0
    assert score_objective("multiple", ["A", "C"], ["C", "A"]) == 1.0
    assert score_objective("multiple", ["A", "C"], ["A", {"bad": "value"}]) == 0.0
    assert score_objective("true_false", False, False) == 1.0
    assert score_objective("true_false", False, 0) == 0.0
