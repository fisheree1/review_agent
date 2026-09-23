"""Make two bounded model calls using synthetic text; print no credentials or payloads."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx

from app.core.config import ModelSettings
from app.rag.domain import Evidence, RagFailure, validate_answer
from app.rag.messages import FAILURES
from app.rag.providers import CloudModels


async def main() -> None:
    settings = ModelSettings()
    async with httpx.AsyncClient(follow_redirects=False) as client:
        models = CloudModels(settings, client)
        failed = False
        try:
            vectors = await models.embed(["The median is robust against extreme outliers."])
            print(f"DashScope OK: model={settings.dashscope_model}, dimensions={len(vectors[0])}")
        except RagFailure as exc:
            failed = True
            print(f"DashScope FAILED: {exc.code}: {FAILURES.get(exc.code, '模型检查失败')}")
        try:
            source = Evidence(uuid4(), "The median is robust against extreme outliers.", 1, {})
            answer, usage = await models.answer(
                "Why is the median useful with extreme outliers?", [source]
            )
            checked = validate_answer(answer, [source])
            if checked["insufficient_evidence"]:
                raise RagFailure("ANSWER_INVALID", "Expected a supported answer")
            print(
                f"DeepSeek OK: model={usage['model']}, citations_valid=True, "
                f"output_tokens={usage['completion_tokens']}"
            )
        except RagFailure as exc:
            failed = True
            print(f"DeepSeek FAILED: {exc.code}: {FAILURES.get(exc.code, '模型检查失败')}")
        if failed:
            raise SystemExit(1) from None


if __name__ == "__main__":
    asyncio.run(main())
