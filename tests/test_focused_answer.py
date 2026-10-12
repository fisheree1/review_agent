import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.learning.agent import StudyPlanningModel
from app.learning.focused_answer import answer_focused
from app.rag.domain import STUDY_PROMPT_VERSION, Evidence, RagFailure
from app.rag.ports import Embeddings


def setup():
    source = Evidence(
        uuid4(),
        "The median resists outliers.",
        1,
        {"kind": "page", "position": 1},
        document_id=uuid4(),
        version_id=1,
    )
    model = AsyncMock(spec=StudyPlanningModel)
    model.answer_study.return_value = (
        {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "Median is robust.",
                    "title": "Median",
                    "explanation": (
                        "Sorting and selecting the middle value limits the effect of extremes."
                    ),
                    "citations": [{"source_id": str(source.id), "quote": source.content}],
                }
            ],
        },
        {"prompt_tokens": 40, "completion_tokens": 20, "prompt_version": STUDY_PROMPT_VERSION},
    )
    embeddings = AsyncMock(spec=Embeddings)
    embeddings.embed.return_value = [[1.0] + [0.0] * 1023]
    search = AsyncMock(return_value=[source])
    active = AsyncMock()
    return source, model, embeddings, search, active


def test_focused_answer_uses_one_generation_and_keeps_valid_source_and_explanation():
    source, model, embeddings, search, active = setup()
    result, usage = asyncio.run(
        answer_focused(
            "Explain median",
            [],
            embeddings=embeddings,
            model=model,
            search=search,
            ensure_active=active,
        )
    )
    model.plan_step.assert_not_awaited()
    model.answer_study.assert_awaited_once()
    embeddings.embed.assert_awaited_once_with(["Explain median"], query=True)
    assert usage["model_calls"] == 1 and usage["search_calls"] == 1
    assert result["claims"][0]["citations"][0]["document_id"] == str(source.document_id)
    assert result["claims"][0]["explanation"]


def test_empty_retrieval_refuses_without_generation():
    _, model, embeddings, search, active = setup()
    search.return_value = []
    result, usage = asyncio.run(
        answer_focused(
            "Unknown", [], embeddings=embeddings, model=model, search=search, ensure_active=active
        )
    )
    assert result == {"insufficient_evidence": True, "claims": []}
    assert usage["model_calls"] == 0
    model.answer_study.assert_not_awaited()


@pytest.mark.parametrize("failure", ["citation", "budget", "cancel"])
def test_focused_answer_rejects_invalid_citation_budget_overrun_and_cancel(failure):
    _, model, embeddings, search, active = setup()
    if failure == "citation":
        model.answer_study.return_value[0]["claims"][0]["citations"][0]["source_id"] = str(uuid4())
    elif failure == "budget":
        model.answer_study.return_value[1]["prompt_tokens"] = 20_000
    else:
        active.side_effect = [None, None, RagFailure("AGENT_RUN_INACTIVE", "Cancelled")]
    with pytest.raises(RagFailure):
        asyncio.run(
            answer_focused(
                "Explain",
                [],
                embeddings=embeddings,
                model=model,
                search=search,
                ensure_active=active,
            )
        )
    if failure == "cancel":
        model.answer_study.assert_not_awaited()
