from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import ModelSettings
from app.rag.application import RagProcessor
from app.rag.domain import (
    Evidence,
    RagFailure,
    Scope,
    Task,
    split_content,
    validate_answer,
    validate_vectors,
)
from app.rag.ports import RagStore
from app.rag.providers import CloudModels


def test_chunks_preserve_source_offsets_and_boundaries() -> None:
    units = [(1, "第一段来源。" * 500), (2, "Another slide source.")]
    chunks = split_content(units)
    assert [chunk.ordinal for chunk in chunks] == list(range(1, len(chunks) + 1))
    for chunk in chunks:
        assert dict(units)[chunk.unit][chunk.start : chunk.end] == chunk.content
        assert len(chunk.content) <= 1500
    assert chunks[-1].unit == 2
    with pytest.raises(RagFailure, match="没有可检索"):
        split_content([(1, "   ")])


def test_citations_reject_fabricated_source_and_nonverbatim_quote() -> None:
    source = Evidence(
        uuid4(), "The median is robust against extreme outliers.", 3, {"kind": "page"}
    )
    payload = {
        "insufficient_evidence": False,
        "claims": [
            {
                "text": "Median is robust.",
                "citations": [{"source_id": str(source.id), "quote": source.content}],
            }
        ],
    }
    answer = validate_answer(payload, [source])
    assert answer["claims"][0]["citations"][0]["unit"] == 3
    with pytest.raises(RagFailure):
        validate_answer(payload, [])
    payload["claims"][0]["citations"][0]["quote"] = "A fabricated quotation."  # type: ignore[index]
    with pytest.raises(RagFailure):
        validate_answer(payload, [source])
    assert validate_answer({"insufficient_evidence": True}, [source])["claims"] == []


@pytest.mark.parametrize("vector", [[0.0] * 1024, [1.0] * 512, [float("nan")] * 1024])
def test_invalid_embedding_cannot_enter_index(vector: list[float]) -> None:
    with pytest.raises(RagFailure):
        validate_vectors([vector], 1)


def test_provider_uses_correct_input_types_and_does_not_leak_error_body() -> None:
    requests: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 3:
            return httpx.Response(
                429, json={"error": "private key and document must not be logged"}
            )
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 1024}]})

    async def check() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = CloudModels(ModelSettings(voyage_api_key=SecretStr("test-only")), client)
            await provider.embed(["Document"])
            await provider.embed(["Question"], query=True)
            with pytest.raises(RagFailure) as failure:
                await provider.embed(["Third"])
            assert failure.value.code == "PROVIDER_LIMIT"
            assert "private key" not in str(failure.value)

    asyncio.run(check())
    assert [request["input_type"] for request in requests[:2]] == ["document", "query"]
    assert requests[0]["output_dimension"] == 1024
    assert len(requests) == 3


def test_cancelled_question_never_calls_generation_and_invalid_answer_fails() -> None:
    async def check() -> None:
        store = AsyncMock(spec=RagStore)
        task = Task(uuid4(), Scope(uuid4(), uuid4()), 1, uuid4(), "Question?")
        store.claim_question.return_value = task
        store.active.return_value = False
        embeddings, model = AsyncMock(), AsyncMock()
        processor = RagProcessor(store, embeddings, model)
        assert await processor.process_question()
        embeddings.embed.assert_not_called()
        model.answer.assert_not_called()
        store.active.return_value = True
        embeddings.embed.return_value = [[1.0] * 1024]
        source = Evidence(uuid4(), "Some relevant source text.", 1, {})
        store.retrieve.return_value = [source]
        model.answer.return_value = ({"insufficient_evidence": False, "claims": []}, {})
        await processor.process_question()
        store.fail_question.assert_awaited_with(task, "ANSWER_INVALID")
        store.finish_question.assert_not_called()

    asyncio.run(check())


def test_index_retry_only_embeds_unfinished_batch() -> None:
    async def check() -> None:
        store = AsyncMock(spec=RagStore)
        task = Task(uuid4(), Scope(uuid4(), uuid4()), 1, uuid4())
        store.claim_index.return_value = task
        store.units.return_value = None
        store.pending.return_value = [Evidence(uuid4(), "remaining source", 1, {})]
        store.save_vectors.return_value = True
        embeddings = AsyncMock()
        embeddings.embed.return_value = [[1.0] * 1024]
        await RagProcessor(store, embeddings, AsyncMock()).process_index()
        store.prepare.assert_not_called()
        embeddings.embed.assert_awaited_once_with(["remaining source"])
        store.finish_index.assert_awaited_once_with(task)

    asyncio.run(check())
