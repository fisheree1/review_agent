"""Run a small versioned, synthetic retrieval and citation quality check.

This is a separate opt-in paid test. It does not access private documents or print
the synthetic payload, model response, or API credentials.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.core.config import ModelSettings
from app.rag.domain import Evidence, RagFailure, validate_answer
from app.rag.providers import CloudModels

EVAL_PATH = Path(__file__).resolve().parent.parent / "evals" / "rag-v1.json"


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    magnitude = math.sqrt(sum(value * value for value in left))
    magnitude *= math.sqrt(sum(value * value for value in right))
    return dot / magnitude


async def main() -> int:
    fixture = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    documents = fixture["documents"]
    settings = ModelSettings()
    passes = 0
    tokens = 0
    started = time.monotonic()
    async with httpx.AsyncClient(follow_redirects=False) as client:
        models = CloudModels(settings, client)
        vectors = await models.embed([source["text"] for source in documents])
        queries = await models.embed([case["question"] for case in fixture["cases"]], query=True)
        for index, (case, query) in enumerate(zip(fixture["cases"], queries, strict=True), start=1):
            try:
                ranked = sorted(
                    range(len(documents)),
                    key=lambda offset: cosine(query, vectors[offset]),
                    reverse=True,
                )
                expected = case["expected_source"]
                expected_rank = next(
                    (
                        rank
                        for rank, offset in enumerate(ranked, start=1)
                        if documents[offset]["id"] == expected
                    ),
                    None,
                )
                sources = [
                    Evidence(
                        uuid5(NAMESPACE_URL, documents[offset]["id"]),
                        documents[offset]["text"],
                        offset + 1,
                        {"kind": "page", "position": offset + 1, "title": None, "path": []},
                    )
                    for offset in ranked[:3]
                ]
                payload, usage = await models.answer(case["question"], sources)
                answer = validate_answer(payload, sources)
                tokens += usage["prompt_tokens"] + usage["completion_tokens"]
                cited = {
                    str(item["source_id"])
                    for claim in answer["claims"]
                    for item in claim["citations"]
                }
                valid = (
                    answer["insufficient_evidence"]
                    if expected is None
                    else expected_rank is not None
                    and expected_rank <= 3
                    and str(uuid5(NAMESPACE_URL, expected)) in cited
                )
                # Injection source is always present in context or in the candidate corpus.
                valid = valid and str(uuid5(NAMESPACE_URL, "injection")) not in cited
                passes += bool(valid)
                print(
                    f"case={index} result={'PASS' if valid else 'FAIL'} "
                    f"expected_rank={expected_rank}"
                )
            except RagFailure as exc:
                print(f"case={index} result=FAIL error={exc.code}")
    print(
        f"eval={fixture['version']} pass={passes}/{len(fixture['cases'])} "
        f"generation_tokens={tokens} elapsed_seconds={round(time.monotonic() - started, 1)}"
    )
    return 0 if passes == len(fixture["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
