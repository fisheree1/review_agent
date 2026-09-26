from __future__ import annotations

from collections import Counter
from typing import Any

from app.rag.domain import Evidence, RagFailure

QUIZ_SCHEMA_VERSION = "quiz-cited-v1"
QUIZ_PROMPT_VERSION = "quiz-blueprint-v1"
QUESTION_TYPES = ("single", "multiple", "true_false", "short")
DIFFICULTIES = ("easy", "medium", "hard")


def validate_blueprint(config: dict[str, Any]) -> dict[str, Any]:
    counts = config.get("type_counts")
    if not isinstance(counts, dict) or set(counts) != set(QUESTION_TYPES):
        raise ValueError("type_counts must name all four types")
    if any(type(counts[kind]) is not int or not 0 <= counts[kind] <= 10 for kind in QUESTION_TYPES):
        raise ValueError("Each question count must be between 0 and 10")
    if not 1 <= sum(counts.values()) <= 10:
        raise ValueError("Quiz must contain 1–10 questions")
    difficulty = config.get("difficulty")
    language = config.get("language")
    topic = config.get("topic", "")
    generation_mode = config.get("generation_mode", "standard")
    if generation_mode not in ("standard", "agent"):
        raise ValueError("Invalid generation mode")
    if difficulty not in DIFFICULTIES or language not in ("zh", "en"):
        raise ValueError("Invalid difficulty or language")
    if not isinstance(topic, str) or len(topic.strip()) > 120:
        raise ValueError("Invalid topic")
    blueprint = {
        "type_counts": counts,
        "difficulty": difficulty,
        "language": language,
        "topic": topic.strip(),
        "schema_version": QUIZ_SCHEMA_VERSION,
    }
    # Preserve old standard-mode configs for idempotent retries across the rollout.
    if generation_mode == "agent":
        blueprint["generation_mode"] = generation_mode
    return blueprint


def validate_candidates(
    payload: dict[str, Any], config: dict[str, Any], sources: list[Evidence]
) -> list[dict[str, Any]]:
    """Reject malformed or unsupported candidates before any question is published."""
    candidates = payload.get("questions")
    if not isinstance(candidates, list) or len(candidates) > 20:
        raise RagFailure("QUIZ_INVALID", "生成题目格式错误")
    available = {str(source.id): source for source in sources}
    accepted: list[dict[str, Any]] = []
    used_stems: set[str] = set()
    counts: Counter[str] = Counter()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        kind = candidate.get("kind")
        stem = candidate.get("stem")
        options = candidate.get("options")
        answer = candidate.get("answer")
        explanation = candidate.get("explanation")
        topic = candidate.get("topic")
        citations = candidate.get("citations")
        if kind not in QUESTION_TYPES or counts[kind] >= config["type_counts"][kind]:
            continue
        if (
            not isinstance(stem, str)
            or not 8 <= len(stem.strip()) <= 1000
            or stem.strip().casefold() in used_stems
        ):
            continue
        if (
            not isinstance(explanation, str)
            or not 8 <= len(explanation.strip()) <= 1500
            or not isinstance(topic, str)
            or not 1 <= len(topic.strip()) <= 120
        ):
            continue
        if kind in ("single", "multiple"):
            if (
                not isinstance(options, list)
                or not 2 <= len(options) <= 6
                or any(
                    not isinstance(option, str) or not option.strip() or len(option) > 200
                    for option in options
                )
                or len({option.strip().casefold() for option in options}) != len(options)
            ):
                continue
            if kind == "single" and (not isinstance(answer, str) or answer not in options):
                continue
            if kind == "multiple" and (
                not isinstance(answer, list)
                or not 2 <= len(answer) < len(options)
                or len(set(map(str, answer))) != len(answer)
                or any(option not in options for option in answer)
            ):
                continue
            if kind == "single":
                correct_options = [str(answer)]
            elif isinstance(answer, list):
                correct_options = [str(option) for option in answer]
            else:
                continue
            if any(option.casefold() in stem.casefold() for option in correct_options):
                continue
        elif kind == "true_false":
            if type(answer) is not bool or options not in ([], None):
                continue
            options = []
        else:
            if (
                not isinstance(answer, str)
                or not 1 <= len(answer.strip()) <= 500
                or options not in ([], None)
            ):
                continue
            options = []
        if not isinstance(citations, list) or not 1 <= len(citations) <= 3:
            continue
        checked: list[dict[str, Any]] = []
        for citation in citations:
            if not isinstance(citation, dict):
                break
            source = available.get(str(citation.get("source_id")))
            quote = citation.get("quote")
            if (
                source is None
                or source.document_id is None
                or source.version_id is None
                or not isinstance(quote, str)
                or not 8 <= len(quote) <= 800
                or quote not in source.content
            ):
                break
            checked.append(
                {
                    "source_id": str(source.id),
                    "document_id": str(source.document_id),
                    "version_id": source.version_id,
                    "unit": source.unit,
                    "locator": source.locator,
                    "quote": quote,
                }
            )
        if len(checked) != len(citations):
            continue
        accepted.append(
            {
                "kind": kind,
                "difficulty": config["difficulty"],
                "topic": topic.strip(),
                "stem": stem.strip(),
                "options": options,
                "answer": answer,
                "explanation": explanation.strip(),
                "sources": checked,
                "schema_version": QUIZ_SCHEMA_VERSION,
            }
        )
        counts[kind] += 1
        used_stems.add(stem.strip().casefold())
    return accepted


def score_objective(kind: str, correct: Any, response: Any) -> float:
    if kind == "single":
        return 1.0 if isinstance(response, str) and response == correct else 0.0
    if kind == "multiple":
        return (
            1.0
            if isinstance(response, list)
            and all(isinstance(item, str) for item in response)
            and set(response) == set(correct)
            and len(response) == len(correct)
            else 0.0
        )
    if kind == "true_false":
        return 1.0 if type(response) is bool and response is correct else 0.0
    raise ValueError("Short answers need model grading")
