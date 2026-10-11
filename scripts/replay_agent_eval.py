"""Replay recorded model responses through the real study and Quiz generation code.

A cassette fixes the selected sources and the model responses in call order. Replaying it
re-runs evidence selection, validation, repair and budgets without network access, and
reports any change in outcome, call sequence or cited sources. Committed cassettes in
``evals/replay`` are synthetic. ``--record --live`` calls paid providers and writes the
result only to the ignored ``evals/local/replay`` directory, because real responses carry
document text and model payloads. Output is content-free: case IDs, outcomes and counts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import ModelSettings
from app.learning.agent import AgentBudget
from app.learning.domain import validate_blueprint
from app.learning.focused_answer import answer_focused
from app.learning.quiz_generation import generate_scoped_quiz
from app.learning.study_generation import generate_study
from app.rag.domain import Evidence, RagFailure
from app.rag.providers import CloudModels

ROOT = Path(__file__).resolve().parent.parent
COMMITTED_DIR = ROOT / "evals" / "replay"
LOCAL_DIR = ROOT / "evals" / "local" / "replay"
CallKind = Literal["answer_study", "generate_quiz"]


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=5000)
    page: int = Field(default=1, ge=1)


class RecordedCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: CallKind
    payload: dict[str, Any]
    usage: dict[str, Any]


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["published", "insufficient_evidence", "failed"]
    failure_code: str | None = None
    calls: list[CallKind]
    cited_sources: list[str] = Field(default_factory=list)
    accepted_questions: int | None = None
    repair_calls: int | None = None


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["focused", "overview", "quiz"]
    question: str = ""
    config: dict[str, Any] | None = None
    sources: list[Source] = Field(max_length=12)
    calls: list[RecordedCall] = Field(default_factory=list)
    expect: Expectation


class Cassette(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    cases: list[Case]


class ReplayMismatch(Exception):
    """The code under test made a call the cassette did not record."""


def source_id(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"replay-source:{key}")


def evidence(case: Case) -> list[Evidence]:
    return [
        Evidence(
            source_id(item.key),
            item.text,
            item.page,
            {"kind": "page", "position": item.page, "title": None, "path": []},
            document_id=uuid5(NAMESPACE_URL, "replay-document"),
            version_id=1,
        )
        for item in case.sources
    ]


def resolve_source_keys(value: Any) -> Any:
    """Readable ``__key__`` placeholders in cassettes become the replayed source IDs."""
    if isinstance(value, dict):
        return {key: resolve_source_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_source_keys(item) for item in value]
    if (
        isinstance(value, str)
        and len(value) > 4
        and value.startswith("__")
        and value.endswith("__")
    ):
        return str(source_id(value[2:-2]))
    return value


class ReplayModels:
    def __init__(self, calls: list[RecordedCall]) -> None:
        self.pending = list(calls)
        self.made: list[CallKind] = []

    def _next(self, kind: CallKind) -> tuple[dict[str, Any], dict[str, Any]]:
        self.made.append(kind)
        if not self.pending:
            raise ReplayMismatch(f"unrecorded {kind} call")
        call = self.pending.pop(0)
        if call.kind != kind:
            raise ReplayMismatch(f"expected {call.kind}, code called {kind}")
        return resolve_source_keys(json.loads(json.dumps(call.payload))), dict(call.usage)

    async def answer_study(
        self,
        question: str,
        sources: list[Evidence],
        *,
        history: list[dict[str, str]] | None = None,
        mode: Literal["focused", "overview"] = "focused",
        repair_feedback: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._next("answer_study")

    async def generate_quiz_with_usage(
        self, config: dict[str, Any], sources: list[Evidence]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._next("generate_quiz")


class RecordingModels(ReplayModels):
    """Pass calls to a live provider and keep each response for a local cassette."""

    def __init__(self, provider: CloudModels) -> None:
        super().__init__([])
        self.provider = provider
        self.recorded: list[RecordedCall] = []

    async def answer_study(
        self,
        question: str,
        sources: list[Evidence],
        *,
        history: list[dict[str, str]] | None = None,
        mode: Literal["focused", "overview"] = "focused",
        repair_feedback: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.made.append("answer_study")
        payload, usage = await self.provider.answer_study(
            question, sources, history=history, mode=mode, repair_feedback=repair_feedback
        )
        self.recorded.append(RecordedCall(kind="answer_study", payload=payload, usage=usage))
        return payload, usage

    async def generate_quiz_with_usage(
        self, config: dict[str, Any], sources: list[Evidence]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.made.append("generate_quiz")
        payload, usage = await self.provider.generate_quiz_with_usage(config, sources)
        self.recorded.append(RecordedCall(kind="generate_quiz", payload=payload, usage=usage))
        return payload, usage


class FixedEmbeddings:
    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        return [[1.0] + [0.0] * 1023 for _ in texts]


async def execute(case: Case, models: ReplayModels) -> dict[str, Any]:
    sources = evidence(case)
    keys = {source_id(item.key): item.key for item in case.sources}

    async def active() -> None:
        return None

    async def search(vector: list[float], query: str) -> list[Evidence]:
        return sources

    async def quiz_sources(vector: list[float] | None, topic: str) -> list[Evidence]:
        return sources

    result: dict[str, Any] = {"case": case.id}
    try:
        if case.kind == "quiz":
            config = validate_blueprint(case.config or {})
            questions, _ = await generate_scoped_quiz(
                config,
                embeddings=FixedEmbeddings(),
                model=models,
                sources=quiz_sources,
                ensure_active=active,
            )
            cited = {
                citation["source_id"]
                for question in questions
                for citation in question.get("sources", [])
            }
            result.update(outcome="published", accepted_questions=len(questions))
        else:
            if case.kind == "focused":
                answer, usage = await answer_focused(
                    case.question,
                    [],
                    embeddings=FixedEmbeddings(),
                    model=models,
                    search=search,
                    ensure_active=active,
                    reliable=True,
                )
            else:
                answer, usage = await generate_study(
                    case.question,
                    sources,
                    [],
                    model=models,
                    budget=AgentBudget(),
                    ensure_active=active,
                    mode="overview",
                )
            cited = {
                citation["source_id"]
                for claim in answer["claims"]
                for citation in claim["citations"]
            }
            result.update(
                outcome="insufficient_evidence" if answer["insufficient_evidence"] else "published",
                repair_calls=usage.get("repair_calls", 0),
            )
        result["cited_sources"] = sorted(keys[UUID(str(item))] for item in cited)
    except RagFailure as exc:
        result.update(outcome="failed", failure_code=exc.code)
    result["calls"] = list(models.made)
    result["unused_calls"] = len(models.pending)
    return result


def compare(case: Case, result: dict[str, Any]) -> list[str]:
    expected = case.expect
    problems: list[str] = []
    for field in ("outcome", "failure_code", "calls"):
        if result.get(field) != getattr(expected, field):
            problems.append(
                f"{field}: expected {getattr(expected, field)!r}, got {result.get(field)!r}"
            )
    if result.get("unused_calls"):
        problems.append(f"{result['unused_calls']} recorded calls were never made")
    if expected.outcome == "published" and result.get("cited_sources") != sorted(
        expected.cited_sources
    ):
        problems.append(
            f"cited_sources: expected {sorted(expected.cited_sources)}, "
            f"got {result.get('cited_sources')}"
        )
    for field in ("accepted_questions", "repair_calls"):
        wanted = getattr(expected, field)
        if wanted is not None and result.get(field) != wanted:
            problems.append(f"{field}: expected {wanted}, got {result.get(field)}")
    return problems


async def replay_case(case: Case) -> tuple[dict[str, Any], list[str]]:
    try:
        result = await execute(case, ReplayModels(case.calls))
    except ReplayMismatch as exc:
        return {"case": case.id, "outcome": "mismatch"}, [str(exc)]
    return result, compare(case, result)


def cassette_paths(include_local: bool = True) -> list[Path]:
    paths = sorted(COMMITTED_DIR.glob("*.json"))
    if include_local and LOCAL_DIR.is_dir():
        paths += sorted(LOCAL_DIR.glob("*.json"))
    return paths


def load(path: Path) -> Cassette:
    return Cassette.model_validate_json(path.read_text(encoding="utf-8"))


async def record(cassette: Cassette, name: str) -> Path:
    """Re-run each case's inputs against live providers and snapshot the observed result."""
    recorded: list[Case] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(75, connect=10)) as client:
        provider = CloudModels(ModelSettings(), client)
        for case in cassette.cases:
            models = RecordingModels(provider)
            result = await execute(case, models)
            expect = Expectation(
                outcome=result["outcome"],
                failure_code=result.get("failure_code"),
                calls=result["calls"],
                cited_sources=result.get("cited_sources", []),
                accepted_questions=result.get("accepted_questions"),
                repair_calls=result.get("repair_calls"),
            )
            recorded.append(case.model_copy(update={"calls": models.recorded, "expect": expect}))
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    target = LOCAL_DIR / f"{Path(name).stem}.json"
    target.write_text(
        Cassette(version=cassette.version, cases=recorded).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return target


async def replay_all(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for case in load(path).cases:
            result, problems = await replay_case(case)
            rows.append(
                {
                    "cassette": path.name,
                    "case": case.id,
                    "outcome": result.get("outcome"),
                    "model_calls": len(result.get("calls", [])),
                    "ok": not problems,
                    "problems": problems,
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, help="Cassette whose inputs to re-record")
    parser.add_argument("--live", action="store_true", help="Required with --record")
    args = parser.parse_args()
    if args.record:
        if not args.live:
            parser.error("--record calls paid providers; pass --live to confirm")
        target = asyncio.run(record(load(args.record), args.record.name))
        print(json.dumps({"recorded": str(target.relative_to(ROOT))}))
        return 0
    rows = asyncio.run(replay_all(cassette_paths()))
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    return 0 if all(row["ok"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
