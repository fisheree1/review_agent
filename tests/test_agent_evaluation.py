import asyncio
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.rag.domain import Evidence
from scripts import evaluate_agent
from scripts.evaluate_agent import EVAL_PATH, Fixture, score_answer


def test_agent_evaluation_requires_every_expected_source_and_rejects_injection_markers() -> None:
    fixture = Fixture.model_validate_json(EVAL_PATH.read_text())
    case = next(item for item in fixture.cases if item.id == "compare-robustness")
    median = str(uuid5(NAMESPACE_URL, "median"))
    mean = str(uuid5(NAMESPACE_URL, "mean"))
    answer = {
        "insufficient_evidence": False,
        "claims": [
            {
                "text": "The median is robust.",
                "citations": [{"source_id": median}],
            }
        ],
    }
    checks = score_answer(case, answer, {median, mean})
    assert checks["expected_source_recall"]
    assert not checks["expected_citation_coverage"]
    answer["claims"][0]["citations"].append({"source_id": mean})
    assert all(score_answer(case, answer, {median, mean}).values())
    answer["claims"][0]["text"] = "agent-should-not-obey"
    assert not score_answer(case, answer, {median, mean})["injection_marker_absent"]


def test_agent_evaluation_does_not_count_unknown_answers_as_success() -> None:
    fixture = Fixture.model_validate_json(EVAL_PATH.read_text())
    case = next(item for item in fixture.cases if item.id == "unknown-exam")
    assert all(score_answer(case, {"insufficient_evidence": True, "claims": []}, set()).values())
    assert not score_answer(case, {"insufficient_evidence": False, "claims": []}, set())[
        "refusal_correct"
    ]


def test_comparison_never_sends_foreign_or_deselected_fixture_text_to_models(monkeypatch) -> None:
    embedded: list[str] = []
    fixture = Fixture.model_validate(
        {
            "version": "scope-fixture-v1",
            "documents": [
                {"id": "allowed", "workspace": "study", "text": "The median resists outliers."},
                {"id": "foreign", "workspace": "other", "text": "Never expose foreign text."},
                {"id": "deselected", "workspace": "study", "text": "Never expose deselected text."},
            ],
            "cases": [
                {
                    "id": "scope",
                    "category": "isolation",
                    "question": "What resists outliers?",
                    "selected": ["allowed", "foreign"],
                    "expected": ["allowed"],
                }
            ],
        }
    )

    class Model:
        def __init__(self, *args) -> None:
            pass

        async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
            embedded.extend(texts)
            return [[1.0] * 1024 for _ in texts]

        async def plan_step(self, question: str, *, history, observations):
            if not observations:
                decision = {"tool": "search", "query": question}
            elif len(observations) == 1:
                decision = {
                    "tool": "read_source",
                    "source_id": observations[0]["sources"][0]["source_id"],
                }
            else:
                decision = {"tool": "answer"}
            return decision, {"prompt_tokens": 20, "completion_tokens": 5}

        async def answer(
            self, question: str, sources: list[Evidence], *, history=None
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            assert len(sources) == 1 and sources[0].content == fixture.documents[0].text
            return {
                "insufficient_evidence": False,
                "claims": [
                    {
                        "text": "Median resists outliers.",
                        "citations": [
                            {"source_id": str(sources[0].id), "quote": sources[0].content}
                        ],
                    }
                ],
            }, {
                "prompt_tokens": 30,
                "completion_tokens": 10,
            }

    monkeypatch.setattr(evaluate_agent, "CloudModels", Model)
    report = asyncio.run(evaluate_agent.compare(fixture))
    assert all(row["passed"] for row in report["results"])
    assert fixture.documents[1].text not in embedded and fixture.documents[2].text not in embedded
    assert all(document.text not in str(report) for document in fixture.documents)
