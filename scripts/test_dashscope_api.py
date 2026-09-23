"""Test the DashScope embeddings API with a synthetic document.

The API key is loaded from the local .env file and is never printed.
The request does not send any user document content.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.config import ModelSettings
from app.rag.domain import RagFailure
from app.rag.providers import CloudModels


async def main() -> int:
    settings = ModelSettings()

    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            vectors = await CloudModels(settings, client).embed(
                ["Synthetic test text: the median is robust against outliers."]
            )
    except RagFailure as exc:
        print(f"FAIL: {exc.code}")
        return 1
    print("PASS: DashScope API 调用成功")
    print(f"model={settings.dashscope_model}")
    print(f"vectors={len(vectors)} dimensions={len(vectors[0])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
