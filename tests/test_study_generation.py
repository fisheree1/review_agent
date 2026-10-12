from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.learning.agent import AgentBudget, StudyPlanningModel
from app.learning.study_generation import generate_study
from app.rag.domain import AnswerValidationFailure, Evidence, RagFailure, canonical_excerpt


def fixture():
    source = Evidence(
        uuid4(), "Median resists\n   extreme values.", 3, {"kind": "page", "position": 3}
    )
    raw = {
        "insufficient_evidence": False,
        "claims": [
            {
                "title": "Median",
                "text": "Median resists outliers.",
                "explanation": "The middle value is less affected by extremes.",
                "citations": [{"source_id": str(source.id), "quote": source.content}],
            }
        ],
    }
    usage = {"prompt_tokens": 100, "completion_tokens": 50}
    model = AsyncMock(spec=StudyPlanningModel)
    model.answer_study.return_value = (raw, usage)
    return source, raw, usage, model


def run(model, source, **kwargs):
    return asyncio.run(
        generate_study(
            "Explain median",
            [source],
            [],
            model=model,
            budget=AgentBudget(),
            ensure_active=kwargs.pop("ensure_active", AsyncMock()),
            mode="focused",
            **kwargs,
        )
    )


def test_wrapped_pdf_quote_resolves_to_exact_source_without_another_model_call():
    source, raw, _, model = fixture()
    raw["claims"][0]["citations"][0]["quote"] = "Median resists extreme values."
    answer, usage = run(model, source)
    assert answer["claims"][0]["citations"][0]["quote"] == source.content
    assert usage["quote_whitespace_fixes"] == 1
    assert usage["model_calls"] == 1


@pytest.mark.parametrize(
    "quote,source",
    [
        ("value is 10.", "value is 100."),
        ("Value is ten.", "value is ten."),
        ("a b", "a\nb and a\tb"),
        ("median is robust", "median is not robust"),
    ],
)
def test_citation_recovery_never_changes_numbers_case_words_or_ambiguous_locations(quote, source):
    assert canonical_excerpt(quote, source) is None


def test_received_bad_citation_is_repaired_once_and_both_calls_are_charged():
    source, raw, usage, model = fixture()
    invalid = deepcopy(raw)
    invalid["claims"][0]["citations"][0]["quote"] = "Fabricated quotation."
    model.answer_study.side_effect = [(invalid, usage), (raw, usage)]
    recorder = AsyncMock()
    answer, total = run(model, source, record_validation=recorder)
    assert answer["claims"][0]["citations"][0]["quote"] == source.content
    assert total["model_calls"] == 2 and total["cost_units"] == 600
    assert total["repair_calls"] == 1
    feedback = model.answer_study.call_args.kwargs["repair_feedback"]
    assert feedback == {"reason": "citation_quote_not_exact", "point": 1, "citation": 1}
    assert source.content not in str(feedback) and "Fabricated" not in str(feedback)
    recorder.assert_awaited_once()


def test_two_invalid_answers_fail_without_a_third_call_or_uncited_publication():
    source, raw, _, model = fixture()
    raw["claims"][0]["citations"][0]["source_id"] = str(uuid4())
    recorder = AsyncMock()
    with pytest.raises(AnswerValidationFailure) as exc:
        run(model, source, record_validation=recorder)
    assert exc.value.reason == "citation_source_unknown"
    assert model.answer_study.await_count == 2
    assert [call.args[0] for call in recorder.await_args_list] == [0, 1]


def test_unknown_provider_request_is_never_retried():
    source, _, _, model = fixture()
    model.answer_study.side_effect = RagFailure("PROVIDER_TIMEOUT", "Unknown request")
    with pytest.raises(RagFailure) as exc:
        run(model, source)
    assert exc.value.code == "PROVIDER_TIMEOUT"
    assert model.answer_study.await_count == 1


def test_cancel_before_repair_prevents_another_billed_request():
    source, raw, _, model = fixture()
    raw["claims"][0]["citations"][0]["quote"] = "Not the source."
    active = AsyncMock(side_effect=[None, None, RagFailure("AGENT_RUN_INACTIVE", "Cancelled")])
    with pytest.raises(RagFailure) as exc:
        run(model, source, ensure_active=active)
    assert exc.value.code == "AGENT_RUN_INACTIVE"
    assert model.answer_study.await_count == 1


def test_insufficient_repair_budget_stops_before_billing_another_call():
    source, raw, usage, model = fixture()
    raw["claims"][0]["citations"][0]["quote"] = "Not the source."
    usage.update(prompt_tokens=13_000, completion_tokens=50)
    with pytest.raises(RagFailure) as exc:
        run(model, source)
    assert exc.value.code == "AGENT_BUDGET_EXCEEDED"
    assert model.answer_study.await_count == 1


def test_known_truncated_response_can_be_repaired_with_usage_preserved():
    source, raw, usage, model = fixture()
    model.answer_study.side_effect = [({"_generation_error": "truncated"}, usage), (raw, usage)]
    _, total = run(model, source)
    assert total["model_calls"] == 2
    assert total["answer_validation"][0]["reason"] == "generation_truncated"
