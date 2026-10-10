from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from scripts import evaluate_pdf_agent
from scripts.evaluate_pdf_agent import (
    GoldCase,
    GoldSet,
    ask_case,
    evaluate_case,
    private_path,
    score_answer,
    select_cases,
)

DOC = "00000000-0000-4000-8000-000000000001"
SOURCE = "00000000-0000-4000-8000-000000000002"
SCOPE = [{"document_id": DOC, "version_id": 1, "filename": "synthetic.pdf"}]
LOCATOR = {"kind": "page", "position": 2, "title": None, "path": []}
CONTENT = {2: {"content": "Training uses labelled examples.", "citation_locator": LOCATOR}}


def gold_case(**changes: Any) -> GoldCase:
    return GoldCase.model_validate(
        {
            "id": "labels",
            "category": "definition",
            "question": "Explain supervised learning.",
            "source_page_groups": [[2, 3]],
            "concept_groups": [["label", "标签"]],
            "reference_answer": "It uses labelled targets.",
            "rubric": ["Explain target labels."],
            **changes,
        }
    )


def answer() -> dict[str, Any]:
    return {
        "id": "message",
        "status": "answered",
        "run_id": None,
        "answer": {
            "insufficient_evidence": False,
            "claims": [
                {
                    "title": "Target labels",
                    "text": "The examples have target labels.",
                    "explanation": "Imagine learning from labelled house prices.",
                    "citations": [
                        {
                            "source_id": SOURCE,
                            "document_id": DOC,
                            "version_id": 1,
                            "unit": 2,
                            "locator": LOCATOR,
                            "quote": "labelled examples",
                        }
                    ],
                }
            ],
        },
    }


def test_general_knowledge_explanation_is_allowed_with_source_grounded_core() -> None:
    result = score_answer(gold_case(), answer(), SCOPE, CONTENT)
    assert result["screen_passed"] is True
    assert result["semantic_review"] == "pending"
    assert result["expected_page_group_coverage"] == 1


def test_page_alternatives_allow_equivalent_sources_but_require_each_topic() -> None:
    result = score_answer(gold_case(source_page_groups=[[2, 3], [5]]), answer(), SCOPE, CONTENT)
    assert result["expected_page_group_coverage"] == 0.5
    assert "expected_page_group_not_cited" in result["errors"]


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("version_id", 2, "citation_outside_selected_version"),
        ("document_id", "other-workspace-document", "citation_outside_selected_version"),
        ("quote", "invented quotation", "citation_quote_not_in_source"),
        ("locator", {**LOCATOR, "position": 3}, "citation_locator_mismatch"),
    ],
)
def test_invalid_citations_cannot_count_as_valid_page_hits(
    field: str, value: Any, error: str
) -> None:
    message = answer()
    message["answer"]["claims"][0]["citations"][0][field] = value
    score = score_answer(gold_case(), message, SCOPE, CONTENT)
    assert score["screen_passed"] is False
    assert score["cited_pages"] == []
    assert error in score["errors"]


def test_missing_evidence_case_rejects_a_fluent_invented_answer() -> None:
    case = gold_case(expected="insufficient", source_page_groups=[], concept_groups=[])
    result = score_answer(case, answer(), SCOPE, CONTENT)
    assert "unsupported_claims_on_missing_evidence" in result["errors"]
    message = {"status": "insufficient", "answer": {"insufficient_evidence": True, "claims": []}}
    assert score_answer(case, message, SCOPE, CONTENT)["screen_passed"] is True


def test_gold_validation_rejects_duplicate_ids_and_out_of_range_pages() -> None:
    fixture = {
        "schema_version": "pdf-agent-eval-v1",
        "version": "synthetic-v1",
        "pdf_sha256": "a" * 64,
        "page_count": 3,
        "cases": [gold_case().model_dump()],
    }
    for invalid_cases in (
        [gold_case().model_dump()] * 2,
        [gold_case(source_page_groups=[[4]]).model_dump()],
    ):
        with pytest.raises(ValidationError):
            GoldSet.model_validate({**fixture, "cases": invalid_cases})
    gold = GoldSet.model_validate(fixture)
    with pytest.raises(Exception, match="Unknown or duplicate"):
        select_cases(gold, ["not-present"], 20)
    with pytest.raises(Exception, match="1–20"):
        select_cases(gold, None, 21)


def test_real_reports_cannot_be_written_outside_gitignored_private_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(Exception, match="evals/local"):
        private_path(tmp_path / "answers.json")


def test_evaluator_uses_conversation_agent_and_exact_version_source_endpoint() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/conversations" and request.method == "POST":
            return httpx.Response(201, json={"id": "conversation", "scope": SCOPE})
        if request.method == "POST":
            return httpx.Response(202, json={"id": "message"})
        if request.url.path.endswith("/content"):
            assert request.url.params["version_id"] == "1"
            return httpx.Response(200, json={"contents": [{"ordinal": 2, **CONTENT[2]}]})
        return httpx.Response(200, json={"messages": [deepcopy(answer())]})

    async def run() -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(respond)
        ) as client:
            return await evaluate_case(client, gold_case(), DOC, "eval-run", 60, False)

    result = asyncio.run(run())
    assert result["score"]["screen_passed"] is True
    assert any(request.url.path.endswith("/messages") for request in requests)
    assert all("/questions" not in request.url.path for request in requests)
    assert all(
        request.headers.get("Idempotency-Key") for request in requests if request.method == "POST"
    )


def test_timed_out_case_cancels_outstanding_work_instead_of_resending() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(202, json={"id": "message"})

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(respond)
        ) as client:
            await ask_case(client, "conversation", "A question", "unique-key", 0)

    with pytest.raises(Exception, match="timed out and was cancelled"):
        asyncio.run(run())
    assert requests == [
        "/api/v1/conversations/conversation/messages",
        "/api/v1/conversations/conversation/messages/message:cancel",
    ]


def test_retrieval_measurement_survives_receipt_purge_after_answer_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polls = 0
    observations = 0
    sources = [{"source_id": SOURCE, "page": 2, "document_id": DOC, "version_id": 1}]

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.method == "POST":
            return httpx.Response(202, json={"id": "message"})
        if "/agent-runs/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "status": "running" if polls == 1 else "completed",
                    "stage": "summary" if polls == 1 else "done",
                    "plan": {"summary_mode": "focused"},
                },
            )
        polls += 1
        message = answer()
        message.update(run_id="run", status="processing" if polls == 1 else "answered")
        return httpx.Response(200, json={"messages": [message]})

    async def observe(run_id: str, prior_question: str | None) -> dict[str, Any]:
        nonlocal observations
        observations += 1
        return {
            "retrieval_replay": sources if observations == 1 else None,
            "retrieval_unavailable_reason": None if observations == 1 else "receipt_cleared",
            "usage": {"model_calls": observations},
        }

    async def no_wait(seconds: float) -> None:
        pass

    monkeypatch.setattr(evaluate_pdf_agent, "observe_run", observe)
    monkeypatch.setattr(evaluate_pdf_agent.asyncio, "sleep", no_wait)

    async def run() -> dict[str, Any] | None:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(respond)
        ) as client:
            _, _, _, observation = await ask_case(
                client,
                "conversation",
                "A question",
                "unique-key",
                60,
                observe_db=True,
            )
            return observation

    result = asyncio.run(run())
    assert result is not None
    assert result["retrieval_replay"] == sources
    assert result["usage"]["model_calls"] == 2
    assert result["retrieval_unavailable_reason"] is None


def test_completed_run_refetches_message_if_publication_commits_between_status_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polls = 0
    posted = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal polls, posted
        if request.method == "POST":
            posted += 1
            return httpx.Response(202, json={"id": "message"})
        if "/agent-runs/" in request.url.path:
            return httpx.Response(200, json={"status": "completed", "stage": "done"})
        polls += 1
        message = answer()
        message.update(run_id="run", status="processing" if polls == 1 else "answered")
        return httpx.Response(200, json={"messages": [message]})

    async def no_wait(seconds: float) -> None:
        pass

    monkeypatch.setattr(evaluate_pdf_agent.asyncio, "sleep", no_wait)

    async def run() -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(respond)
        ) as client:
            message, _, _, _ = await ask_case(client, "conversation", "A question", "key", 60)
            return message

    assert asyncio.run(run())["status"] == "answered"
    assert posted == 1 and polls == 2
