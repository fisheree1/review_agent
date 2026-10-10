"""The downloadable note contains persisted cited points and explanation."""

import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader

from app.core.auth import Principal, get_current_principal
from app.core.errors import ApplicationError
from app.core.observability import record_request
from app.learning import api
from app.learning import run_store as run_store_module
from app.learning.pdf_export import render_study_pdf
from app.learning.run_store import SqlAgentRunStore
from app.main import application_error_handler


def material() -> tuple[str, dict, list[dict], dict]:
    return (
        "第一章知识点整理",
        {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "中位数不易受到极端值影响。",
                    "citations": [
                        {"document_id": "source", "unit": 3, "quote": "中位数不易受到极端值影响"}
                    ],
                },
                {
                    "text": "The median is robust to outliers.",
                    "citations": [
                        {
                            "document_id": "source",
                            "unit": 4,
                            "quote": "The median is robust to outliers",
                        }
                    ],
                },
            ],
            "explanation": "极端值会明显拉动平均数，中位数则由排序后的中间位置决定。",
        },
        [{"document_id": "source", "filename": "统计讲义.pdf"}],
        {"sampled_pages": 2, "indexed_pages": 4},
    )


def test_study_pdf_renders_chinese_english_and_source_pages() -> None:
    content = render_study_pdf(*material())
    assert content.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(content))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "中位数" in text and "median is robust" in text
    assert "极端值会明显拉动平均数" in text
    assert "2 / 4" in text and "第 3 页" in text


def test_pdf_route_requires_auth_and_passes_workspace_to_owned_lookup(monkeypatch) -> None:
    async def scenario() -> None:
        workspace, run_id = uuid4(), uuid4()
        lookup = AsyncMock(return_value=material())
        monkeypatch.setattr(api.SqlAgentRunStore, "pdf_material", lookup)
        app = FastAPI()
        app.include_router(api.router)
        app.add_exception_handler(ApplicationError, application_error_handler)
        app.middleware("http")(record_request)
        app.dependency_overrides[api.get_learning_service] = lambda: SimpleNamespace(
            store=SimpleNamespace(sessions=object())
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(f"/api/v1/agent-runs/{run_id}/notes.pdf")).status_code == 401
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            workspace_public_id=workspace, user_public_id=uuid4()
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/agent-runs/{run_id}/notes.pdf")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/pdf"
            assert response.headers["cache-control"] == "private, no-store"
            lookup.assert_awaited_once_with(workspace, run_id)

    asyncio.run(scenario())


def test_pdf_lookup_filters_workspace_before_reading_answer() -> None:
    async def scenario() -> None:
        session = AsyncMock()
        session.scalar.return_value = None

        class Sessions:
            @asynccontextmanager
            async def begin(self):
                yield session

        learning = SimpleNamespace(sessions=Sessions(), _workspace=AsyncMock(return_value=42))
        with pytest.raises(ApplicationError) as exc:
            await SqlAgentRunStore(learning).pdf_material(uuid4(), uuid4())
        assert exc.value.status_code == 404
        statement = str(
            session.scalar.call_args.args[0].compile(compile_kwargs={"literal_binds": True})
        )
        assert "agent_runs.workspace_id = 42" in statement
        assert "agent_runs.public_id" in statement
        session.get.assert_not_awaited()

    asyncio.run(scenario())


def test_pdf_download_is_revoked_when_source_version_changes(monkeypatch) -> None:
    async def scenario() -> None:
        session = AsyncMock()

        class Sessions:
            @asynccontextmanager
            async def begin(self):
                yield session

        learning = SimpleNamespace(sessions=Sessions(), _workspace=AsyncMock(return_value=42))
        store = SqlAgentRunStore(learning)
        monkeypatch.setattr(
            store,
            "_owned",
            AsyncMock(
                return_value=SimpleNamespace(outputs=[{"kind": "pdf"}], scope=[], profile="profile")
            ),
        )
        monkeypatch.setattr(run_store_module, "snapshot_ready", AsyncMock(return_value=False))
        with pytest.raises(ApplicationError) as exc:
            await store.pdf_material(uuid4(), uuid4())
        assert exc.value.status_code == 404
        session.get.assert_not_awaited()

    asyncio.run(scenario())


def test_study_pdf_retains_each_knowledge_points_teaching():
    title, answer, scope, coverage = material()
    answer.pop("explanation")
    answer["claims"][0].update(title="中位数与极端值", explanation="排序后选择中间位置。")
    text = "\n".join(
        page.extract_text()
        for page in PdfReader(BytesIO(render_study_pdf(title, answer, scope, coverage))).pages
    )
    assert "中位数与极端值" in text and "排序后选择中间位置" in text
