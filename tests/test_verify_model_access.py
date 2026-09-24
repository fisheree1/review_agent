from __future__ import annotations

import asyncio

import pytest

from app.rag.domain import RagFailure
from scripts import verify_model_access


def test_embedding_only_checks_one_provider_without_calling_generation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FakeModels:
        async def embed(self, _: list[str]) -> list[list[float]]:
            return [[0.0] * 1024]

        async def answer(self, *_: object) -> None:
            raise AssertionError("Embedding-only verification must not call generation")

    monkeypatch.setattr(verify_model_access, "CloudModels", lambda *_: FakeModels())
    asyncio.run(verify_model_access.main(embedding_only=True))
    output = capsys.readouterr().out
    assert "DashScope OK" in output
    assert "DeepSeek" not in output


def test_embedding_only_reports_provider_failure_without_generation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailedModels:
        async def embed(self, _: list[str]) -> list[list[float]]:
            raise RagFailure("PROVIDER_UNCONFIGURED", "private provider details")

        async def answer(self, *_: object) -> None:
            raise AssertionError("Generation must not run after embedding failure")

    monkeypatch.setattr(verify_model_access, "CloudModels", lambda *_: FailedModels())
    with pytest.raises(SystemExit) as caught:
        asyncio.run(verify_model_access.main(embedding_only=True))
    assert caught.value.code == 1
    assert "private provider details" not in capsys.readouterr().out
