"""Billing recovery must distinguish received results from uncertain requests."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.learning.application import LearningModel
from app.learning.run_application import RecordedCalls, RecordedModels
from app.learning.workflow import AgentRunPersistence
from app.rag.domain import RagFailure


def test_received_over_budget_result_records_actual_charge_before_stage_validation() -> None:
    store = AsyncMock(spec=AgentRunPersistence)
    store.begin_call.return_value = None
    result = ({"claims": []}, {"prompt_tokens": 19000, "completion_tokens": 1})
    provider = AsyncMock(return_value=result)
    calls = RecordedCalls(store, uuid4(), uuid4())
    assert asyncio.run(calls.invoke("answer", provider)) == result
    usage = store.finish_call.call_args.args[-1]
    assert usage["prompt_tokens"] == 19000 and usage["cost_units"] == 19004
    store.reject_call.assert_not_awaited()


def test_returned_call_receipt_recovers_without_another_billed_request() -> None:
    store = AsyncMock(spec=AgentRunPersistence)
    store.begin_call.return_value = [{"action": "answer"}, {"prompt_tokens": 20}]
    provider = AsyncMock()
    calls = RecordedCalls(store, uuid4(), uuid4())
    result = asyncio.run(calls.invoke("plan_task", provider))
    assert result[0]["action"] == "answer"
    provider.assert_not_awaited()
    store.finish_call.assert_not_awaited()


def test_old_answer_receipt_replays_into_new_study_path_without_rebilling() -> None:
    store = AsyncMock(spec=AgentRunPersistence)
    old_answer = ({"insufficient_evidence": True, "claims": []}, {"prompt_tokens": 20})
    store.begin_call.return_value = old_answer
    model = AsyncMock(spec=LearningModel)
    result = asyncio.run(
        RecordedModels(model, RecordedCalls(store, uuid4(), uuid4())).answer_study("Question", [])
    )
    assert result == old_answer
    assert store.begin_call.call_args.args[-1] == "answer"
    model.answer_study.assert_not_awaited()
    store.finish_call.assert_not_awaited()


@pytest.mark.parametrize("code", ["AGENT_PLAN_INVALID", "PROVIDER_TIMEOUT"])
def test_received_invalid_response_and_unknown_timeout_have_distinct_recovery(code: str) -> None:
    store = AsyncMock(spec=AgentRunPersistence)
    store.begin_call.return_value = None
    provider = AsyncMock(side_effect=RagFailure(code, "synthetic failure"))
    calls = RecordedCalls(store, uuid4(), uuid4())
    with pytest.raises(RagFailure):
        asyncio.run(calls.invoke("plan_task", provider))
    provider.assert_awaited_once()
    if code == "AGENT_PLAN_INVALID":
        store.reject_call.assert_awaited_once()
    else:
        store.reject_call.assert_not_awaited()
