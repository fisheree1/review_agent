"""Test the Voyage embeddings API with a synthetic document.

The API key is loaded from the local .env file and is never printed.
The request does not send any user document content.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

MODEL = "voyage-4"
ENDPOINT = "https://api.voyageai.com/v1/embeddings"


async def main() -> int:
    api_key = os.environ.get("VOYAGE_API_KEY", "")
    if not api_key:
        print("FAIL: VOYAGE_API_KEY 未配置")
        return 1

    payload = {
        "model": MODEL,
        "input": ["Synthetic test text: the median is robust against outliers."],
        "input_type": "document",
        "output_dimension": 1024,
        "output_dtype": "float",
        "truncation": False,
    }

    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
    except httpx.TimeoutException:
        print("FAIL: 请求超时")
        return 1
    except httpx.HTTPError as exc:
        print(f"FAIL: 网络请求错误（{type(exc).__name__}）")
        return 1

    if response.status_code >= 400:
        messages = {
            401: "密钥无效或没有权限",
            403: "密钥无效或没有权限",
            429: "请求频率或额度受限",
        }
        message = messages.get(response.status_code, "Voyage 请求失败")
        print(f"FAIL: HTTP {response.status_code}：{message}")
        return 1

    try:
        result: dict[str, Any] = response.json()
        vectors = result["data"]
        embedding = vectors[0]["embedding"]
        returned_model = result.get("model", MODEL)
        usage = result.get("usage", {})
        if len(vectors) != 1 or len(embedding) != 1024:
            print("FAIL: 返回向量维度或数量不符合预期")
            return 1
    except (KeyError, IndexError, TypeError, ValueError):
        print("FAIL: Voyage 返回格式不符合预期")
        return 1

    print("PASS: Voyage API 调用成功")
    print(f"model={returned_model}")
    print(f"vectors=1 dimensions={len(embedding)}")
    print(f"total_tokens={usage.get('total_tokens', 'unknown')}")
    return 0


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    sys.exit(asyncio.run(main()))
