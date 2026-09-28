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


def test_bilingual_quiz_publishes_only_fully_paired_questions() -> None:
    source = evidence()
    config = blueprint()
    assert config["schema_version"] == "quiz-cited-v1"
    config = validate_blueprint({**config, "language": "zh-en"})
    assert config["schema_version"] == "quiz-cited-v2"
    valid = {
        **candidate(source),
        "topic": "中文：统计量\nEnglish: Statistic",
        "stem": "中文：哪个统计量能抵抗极端值？\nEnglish: Which statistic resists outliers?",
        "options": [
            "中文：中位数\nEnglish: Median",
            "中文：均值\nEnglish: Mean",
            "中文：众数\nEnglish: Mode",
        ],
        "answer": "中文：中位数\nEnglish: Median",
        "explanation": (
            "中文：资料指出中位数不易受极端值影响。\n"
            "English: The source says the median resists outliers."
        ),
    }
    missing_translation = {**valid, "explanation": "资料指出中位数不易受极端值影响。"}
    accepted = validate_candidates({"questions": [missing_translation, valid]}, config, [source])
    assert len(accepted) == 1
    assert accepted[0]["answer"] == valid["answer"]
