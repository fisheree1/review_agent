from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import ModelSettings, RagSettings
from app.rag.application import RagProcessor
from app.rag.domain import (
    Evidence,
    RagFailure,
    Scope,
    Task,
    split_content,
    validate_answer,
    validate_study_answer,
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


def test_study_explanation_keeps_pdf_points_cited_and_refuses_missing_sources() -> None:
    source = Evidence(uuid4(), "The median resists extreme outliers.", 3, {"kind": "page"})
    payload = {
        "insufficient_evidence": False,
        "claims": [
            {
                "text": "The median resists outliers.",
                "citations": [{"source_id": str(source.id), "quote": source.content}],
            }
        ],
        "explanation": "  Sort the values first; the middle position determines the median.  ",
    }
    answer = validate_study_answer(payload, [source])
    assert answer["explanation"] == (
        "Sort the values first; the middle position determines the median."
    )
    assert "explanation" not in validate_answer(payload, [source])
    with pytest.raises(RagFailure):
        validate_study_answer(payload, [])
    with pytest.raises(RagFailure):
        validate_study_answer({**payload, "explanation": " "}, [source])
    with pytest.raises(RagFailure):
        validate_study_answer(
            {"insufficient_evidence": False, "claims": payload["claims"]},
            [source],
            require_explanation=True,
        )
    assert validate_study_answer({**payload, "insufficient_evidence": True}, []) == {
        "insufficient_evidence": True,
        "claims": [],
    }


def test_study_generation_allows_general_explanation_without_changing_reader_rag() -> None:
    prompts: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompts.append(body["messages"][0]["content"])
        return httpx.Response(
            200,
            json={
                "model": "test",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"insufficient_evidence": True, "claims": []})
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    async def check() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = CloudModels(ModelSettings(deepseek_api_key=SecretStr("test-only")), client)
            _, study_usage = await provider.answer_study("Explain the PDF", [])
            _, reader_usage = await provider.answer("Explain the PDF", [])
            assert study_usage["prompt_version"] != reader_usage["prompt_version"]

    asyncio.run(check())
    assert "You may use general knowledge" in prompts[0]
    assert "Do not use outside knowledge" in prompts[1]


@pytest.mark.parametrize("vector", [[0.0] * 1024, [1.0] * 512, [float("nan")] * 1024])
def test_invalid_embedding_cannot_enter_index(vector: list[float]) -> None:
    with pytest.raises(RagFailure):
        validate_vectors([vector], 1)


def test_dashscope_provider_uses_correct_text_types_and_does_not_leak_error_body() -> None:
    requests: list[dict] = []
    endpoint = (
        "https://ws-test.cn-beijing.maas.aliyuncs.com"
        "/api/v1/services/embeddings/text-embedding/text-embedding"
    )

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == endpoint
        assert request.headers["Authorization"] == "Bearer test-only"
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 3:
            return httpx.Response(
                429, json={"error": "private key and document must not be logged"}
            )
        return httpx.Response(
            200,
            json={"output": {"embeddings": [{"text_index": 0, "embedding": [1.0] * 1024}]}},
        )

    async def check() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = CloudModels(
                ModelSettings(
                    dashscope_api_key=SecretStr("test-only"),
                    dashscope_embedding_url=endpoint,
                ),
                client,
            )
            await provider.embed(["Document"])
            await provider.embed(["Question"], query=True)
            with pytest.raises(RagFailure) as failure:
                await provider.embed(["Third"])
            assert failure.value.code == "PROVIDER_LIMIT"
            assert "private key" not in str(failure.value)

    asyncio.run(check())
    assert [request["parameters"]["text_type"] for request in requests[:2]] == [
        "document",
        "query",
    ]
    assert requests[0]["model"] == "qwen3.7-text-embedding"
    assert requests[0]["input"] == {"texts": ["Document"]}
    assert requests[0]["parameters"]["dimension"] == 1024
    assert len(requests) == 3


def test_dashscope_url_is_restricted_and_new_profile_does_not_reuse_voyage_vectors() -> None:
    with pytest.raises(ValidationError):
        ModelSettings(dashscope_embedding_url="https://example.com/embeddings")
    assert RagSettings().profile.startswith("dashscope:qwen3.7-text-embedding:1024:")


def test_dashscope_embedding_rejects_missing_and_duplicate_indexes() -> None:
    endpoint = (
        "https://ws-test.cn-beijing.maas.aliyuncs.com"
        "/api/v1/services/embeddings/text-embedding/text-embedding"
    )

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [
                        {"text_index": 0, "embedding": [1.0] * 1024},
                        {"text_index": 0, "embedding": [1.0] * 1024},
                    ]
                }
            },
        )

    async def check() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = CloudModels(
                ModelSettings(
                    dashscope_api_key=SecretStr("test-only"),
                    dashscope_embedding_url=endpoint,
                ),
                client,
            )
            with pytest.raises(RagFailure) as failure:
                await provider.embed(["first", "second"])
            assert failure.value.code == "EMBEDDING_INVALID"

    asyncio.run(check())


def test_dashscope_batch_vectors_follow_input_order() -> None:
    endpoint = (
        "https://ws-test.cn-beijing.maas.aliyuncs.com"
        "/api/v1/services/embeddings/text-embedding/text-embedding"
    )

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [
                        {"text_index": 1, "embedding": [2.0] * 1024},
                        {"text_index": 0, "embedding": [1.0] * 1024},
                    ]
                }
            },
        )

    async def check() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = CloudModels(
                ModelSettings(
                    dashscope_api_key=SecretStr("test-only"),
                    dashscope_embedding_url=endpoint,
                ),
                client,
            )
            vectors = await provider.embed(["first", "second"])
            assert [vector[0] for vector in vectors] == [1.0, 2.0]

    asyncio.run(check())


def test_planner_uses_bounded_json_decisions_without_exposing_other_tools() -> None:
    requests: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "model": "fake-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"tool":"search","query":"median"}'},
                    }
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 9},
            },
        )

    async def check() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = CloudModels(ModelSettings(deepseek_api_key=SecretStr("test-only")), client)
            decision, usage = await provider.plan_step(
                "What does the material say?", history=[], observations=[]
            )
            assert decision == {"tool": "search", "query": "median"}
            assert usage["prompt_tokens"] == 40
            assert usage["completion_tokens"] == 9

    asyncio.run(check())
    assert requests[0]["max_tokens"] == 256
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "delete" not in requests[0]["messages"][0]["content"]


def test_standard_quiz_still_accepts_payload_when_provider_omits_usage() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"questions":[]}'}}],
                "usage": None,
            },
        )

    async def check() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = CloudModels(ModelSettings(deepseek_api_key=SecretStr("test-only")), client)
            assert await provider.generate_quiz({}, []) == {"questions": []}
            _, usage = await provider.generate_quiz_with_usage({}, [])
            assert usage["prompt_tokens"] == 0

    asyncio.run(check())


def test_bilingual_quiz_prompt_requires_paired_fields_without_translating_citations() -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"questions":[]}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            },
        )

    async def check() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = CloudModels(ModelSettings(deepseek_api_key=SecretStr("test-only")), client)
            await provider.generate_quiz_with_usage({"language": "zh-en"}, [])

    asyncio.run(check())
    assert requests[0]["max_tokens"] == 8000
    assert "Citation quotes remain exact source substrings" in requests[0]["messages"][0]["content"]


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


def test_knowledge_sections_resolve_pages_from_scoped_evidence_and_reject_invented_sources():
    from app.learning.api import StudyAnswerResponse

    source = Evidence(
        uuid4(),
        "The median resists extreme outliers.",
        13,
        {"kind": "page", "position": 13, "title": None, "path": []},
        document_id=uuid4(),
        version_id=2,
    )
    point = {
        "title": " Median ",
        "text": "Median resists outliers.",
        "explanation": " Sort the values and select the middle one. ",
        "citations": [{"source_id": str(source.id), "quote": source.content, "unit": 999}],
    }
    payload = {"insufficient_evidence": False, "claims": [point]}
    answer = validate_study_answer(payload, [source], require_sections=True)
    saved = StudyAnswerResponse.model_validate(answer).model_dump(mode="json")
    assert saved["claims"][0]["title"] == "Median"
    assert saved["claims"][0]["explanation"] == "Sort the values and select the middle one."
    citation = saved["claims"][0]["citations"][0]
    assert citation["unit"] == 13 and citation["version_id"] == 2
    assert citation["document_id"] == str(source.document_id)
    with pytest.raises(RagFailure):
        validate_study_answer(payload, [], require_sections=True)
    point["citations"][0]["source_id"] = str(uuid4())
    with pytest.raises(RagFailure):
        validate_study_answer(payload, [source], require_sections=True)


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"title": "Median"},
        {"title": " ", "explanation": "Example"},
        {"title": "Median", "explanation": " "},
        {"title": "x" * 121, "explanation": "Example"},
        {"title": "Median", "explanation": "x" * 4001},
    ],
)
def test_new_study_schema_rejects_missing_or_unbounded_section_teaching(fields):
    source = Evidence(uuid4(), "The median resists extreme outliers.", 1, {"kind": "page"})
    payload = {
        "insufficient_evidence": False,
        "claims": [
            {
                "text": "Median",
                "citations": [{"source_id": str(source.id), "quote": source.content}],
                **fields,
            }
        ],
    }
    with pytest.raises(RagFailure):
        validate_study_answer(payload, [source], require_sections=True)


def test_section_teaching_budget_is_shared_across_points():
    source = Evidence(uuid4(), "The median resists extreme outliers.", 1, {"kind": "page"})
    point = {
        "title": "Median",
        "text": "Median",
        "explanation": "x" * 2001,
        "citations": [{"source_id": str(source.id), "quote": source.content}],
    }
    with pytest.raises(RagFailure):
        validate_study_answer(
            {"insufficient_evidence": False, "claims": [point, point]},
            [source],
            require_sections=True,
        )
