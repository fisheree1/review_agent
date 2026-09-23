"""Bounded CA6000 API evaluation; private reports stay under evals/local/.

The default mode only checks available API capabilities and source files. --live
uploads three selected lectures, indexes them through the normal Worker, and
asks four questions. Live mode sends lecture text to the configured providers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from app.core.config import ModelSettings, RagSettings

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "evals" / "ca6000-rag-v1.json"
TERMINAL_DOCUMENT = {"ready", "failed"}
TERMINAL_INDEX = {"ready", "failed"}
TERMINAL_QUESTION = {"answered", "insufficient", "failed", "cancelled"}


class EvaluationError(Exception):
    """A sanitized evaluation or API failure."""


def source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capabilities(openapi: dict[str, Any]) -> dict[str, str]:
    paths = openapi.get("paths", {})
    if not isinstance(paths, dict):
        raise EvaluationError("OpenAPI paths are invalid")
    question_posts = [
        operation
        for path, methods in paths.items()
        if "/questions" in path and isinstance(methods, dict)
        for method, operation in methods.items()
        if method == "post" and isinstance(operation, dict)
    ]
    scope_fields = {"document_ids", "collection_ids", "scope"}
    schemas = openapi.get("components", {}).get("schemas", {})
    multi_scope = False
    for operation in question_posts:
        content = operation.get("requestBody", {}).get("content", {})
        schema = content.get("application/json", {}).get("schema", {})
        if "$ref" in schema:
            schema = schemas.get(schema["$ref"].rsplit("/", 1)[-1], {})
        if scope_fields.intersection(schema.get("properties", {})):
            multi_scope = True
    result = {
        "multi_document_scope": multi_scope,
        "continuous_conversation": any(
            "conversations" in path and "post" in methods
            for path, methods in paths.items()
            if isinstance(methods, dict)
        ),
        "answer_feedback": any(
            "feedback" in path and "post" in methods
            for path, methods in paths.items()
            if isinstance(methods, dict)
        ),
    }
    return {
        name: "API_PRESENT_NEEDS_BEHAVIOR_TEST" if present else "NOT_IMPLEMENTED"
        for name, present in result.items()
    }


def score_answer(
    case: dict[str, Any],
    question: dict[str, Any],
    source_by_unit: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    answer = question.get("answer") or {}
    claims = answer.get("claims") or []
    if case["expected"] == "insufficient":
        if question.get("status") != "insufficient" or not answer.get("insufficient_evidence"):
            errors.append("expected_insufficient_evidence")
        if claims:
            errors.append("insufficient_answer_contains_claims")
        return {"passed": not errors, "errors": errors, "locator_hit": None}

    if question.get("status") != "answered" or answer.get("insufficient_evidence"):
        errors.append("expected_cited_answer")
    if not claims:
        errors.append("answer_has_no_claims")
    target = case["locator"]
    locator_hit = False
    for claim in claims:
        citations = claim.get("citations") or []
        if not citations:
            errors.append("claim_has_no_citation")
        for citation in citations:
            if not citation.get("source_id"):
                errors.append("citation_has_no_source_id")
            unit = citation.get("unit")
            source = source_by_unit.get(unit)
            if source is None:
                errors.append("citation_source_unavailable")
                continue
            locator = citation.get("locator") or {}
            source_locator = source.get("citation_locator") or {}
            if locator != source_locator:
                errors.append("citation_locator_mismatch")
            if not citation.get("quote") or citation["quote"] not in source.get("content", ""):
                errors.append("citation_quote_not_in_source")
            if (
                locator.get("kind") == target["kind"]
                and locator.get("position") == target["position"]
            ):
                locator_hit = True
    if not locator_hit:
        errors.append("expected_locator_not_cited")
    claim_text = " ".join(str(claim.get("text", "")) for claim in claims).casefold()
    for index, alternatives in enumerate(case["required_term_groups"]):
        if not any(term.casefold() in claim_text for term in alternatives):
            errors.append(f"required_concept_{index + 1}_missing")
    return {"passed": not errors, "errors": sorted(set(errors)), "locator_hit": locator_hit}


async def api_json(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    try:
        response = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise EvaluationError(f"{method} {path}: {type(exc).__name__}") from exc
    if not response.is_success:
        raise EvaluationError(f"{method} {path}: HTTP {response.status_code}")
    return response.json() if response.content else None


async def wait_for_status(
    client: httpx.AsyncClient,
    path: str,
    terminal: set[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = await api_json(client, "GET", path)
        if not isinstance(result, dict):
            raise EvaluationError(f"GET {path}: invalid response shape")
        if result["status"] in terminal:
            return result
        await asyncio.sleep(2)
    raise EvaluationError(f"GET {path}: timed out after {timeout_seconds}s")


def private_report(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"ca6000-{report['run_id']}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return path


async def evaluate_case(
    client: httpx.AsyncClient,
    case: dict[str, Any],
    document_id: str,
    run_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.monotonic()
    posted = await api_json(
        client,
        "POST",
        f"/api/v1/documents/{document_id}/questions",
        headers={"Idempotency-Key": f"ca6000-{run_id}-{case['id']}"},
        json={"question": case["question"]},
    )
    question = await wait_for_status(
        client,
        f"/api/v1/documents/{document_id}/questions/{posted['id']}",
        TERMINAL_QUESTION,
        timeout_seconds,
    )
    source_by_unit: dict[int, dict[str, Any]] = {}
    for claim in (question.get("answer") or {}).get("claims") or []:
        for citation in claim.get("citations") or []:
            unit = citation.get("unit")
            if isinstance(unit, int) and unit not in source_by_unit:
                content = await api_json(
                    client,
                    "GET",
                    f"/api/v1/documents/{document_id}/content",
                    params={"ordinal": unit},
                )
                source_by_unit[unit] = next(
                    (entry for entry in content["contents"] if entry["ordinal"] == unit),
                    {},
                )
    score = score_answer(case, question, source_by_unit)
    return {
        "id": case["id"],
        "status": question["status"],
        "duration_seconds": round(time.monotonic() - started, 2),
        "score": score,
        "question": case["question"],
        "answer_for_manual_review": question.get("answer"),
    }


async def run(args: argparse.Namespace) -> int:
    parsed = urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise EvaluationError("Only a local HTTP API is allowed")
    fixture = json.loads(MANIFEST.read_text(encoding="utf-8"))
    documents = [
        item for item in fixture["documents"] if not args.document or item["id"] in args.document
    ]
    if args.document and (
        len(args.document) != len(set(args.document)) or len(documents) != len(args.document)
    ):
        raise EvaluationError("Unknown or repeated document selection")
    cases = [
        case for case in fixture["cases"] if case["document"] in {item["id"] for item in documents}
    ]
    if len(documents) > 3 or len(cases) > 4:
        raise EvaluationError("The live run is capped at three documents and four questions")
    paths = {item["id"]: args.course_dir / item["filename"] for item in documents}
    for path in paths.values():
        if not path.is_file() or path.stat().st_size > 25 * 1024 * 1024:
            raise EvaluationError(f"Missing or oversized source: {path.name}")
    inventory = {
        item["id"]: {
            "sha256": source_hash(paths[item["id"]]),
            "bytes": paths[item["id"]].stat().st_size,
        }
        for item in documents
    }
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as client:
        openapi = await api_json(client, "GET", "/openapi.json")
        available = capabilities(openapi)
    print(
        json.dumps(
            {"version": fixture["version"], "documents": inventory, "capabilities": available},
            indent=2,
        ),
        flush=True,
    )
    if not args.live:
        return 0

    token = dotenv_values(ROOT / ".env").get("LOCAL_API_TOKEN")
    if not token:
        raise EvaluationError("LOCAL_API_TOKEN is missing from the local .env")
    report: dict[str, Any] = {
        "version": fixture["version"],
        "run_id": str(uuid4()),
        "capabilities": available,
        "documents": inventory,
        "models": {
            "embedding": ModelSettings().dashscope_model,
            "answer": ModelSettings().deepseek_model,
            "profile": RagSettings().profile,
        },
        "cases": [],
        "cleanup": [],
    }
    created: list[str] = []
    failure: str | None = None
    async with httpx.AsyncClient(
        base_url=args.base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    ) as client:
        try:
            for item in documents:
                source_path = paths[item["id"]]
                with source_path.open("rb") as source:
                    uploaded = await api_json(
                        client,
                        "POST",
                        "/api/v1/documents",
                        headers={"Idempotency-Key": f"ca6000-{report['run_id']}-{item['id']}"},
                        files={"file": (item["filename"], source, item["media_type"])},
                    )
                document_id = uploaded["document"]["id"]
                if not uploaded["deduplicated"]:
                    created.append(document_id)
                state = await wait_for_status(
                    client,
                    f"/api/v1/documents/{document_id}",
                    TERMINAL_DOCUMENT,
                    args.timeout_seconds,
                )
                if state["status"] != "ready":
                    raise EvaluationError(
                        f"document {item['id']} failed: {state.get('failure_code')}"
                    )
                for attempt in range(2 if args.retry_provider_limit else 1):
                    await api_json(
                        client,
                        "POST",
                        f"/api/v1/documents/{document_id}/index",
                        headers={
                            "Idempotency-Key": (
                                f"ca6000-{report['run_id']}-{item['id']}-index-{attempt}"
                            )
                        },
                    )
                    state = await wait_for_status(
                        client,
                        f"/api/v1/documents/{document_id}/index",
                        TERMINAL_INDEX,
                        args.timeout_seconds,
                    )
                    if state["status"] == "ready":
                        break
                    if state.get("failure_code") != "PROVIDER_LIMIT" or attempt != 0:
                        break
                    print("index_rate_limited=waiting_before_one_retry", flush=True)
                    await asyncio.sleep(args.limit_wait_seconds)
                if state["status"] != "ready":
                    raise EvaluationError(f"index {item['id']} failed: {state.get('failure_code')}")
                for case in cases:
                    if case["document"] != item["id"]:
                        continue
                    result = await evaluate_case(
                        client, case, document_id, report["run_id"], args.timeout_seconds
                    )
                    report["cases"].append(result)
                    case_outcome = "PASS" if result["score"]["passed"] else "FAIL"
                    print(f"case={case['id']} result={case_outcome}", flush=True)
        except EvaluationError as exc:
            failure = str(exc)
        except Exception as exc:
            # At this top-level boundary, preserve cleanup and a private failure report.
            failure = f"UNEXPECTED_{type(exc).__name__}"
        finally:
            if not args.keep_documents:
                for document_id in reversed(created):
                    try:
                        await api_json(client, "DELETE", f"/api/v1/documents/{document_id}")
                        response = await client.get(f"/api/v1/documents/{document_id}")
                        report["cleanup"].append(
                            {"document_id": document_id, "deleted": response.status_code == 404}
                        )
                    except (EvaluationError, httpx.HTTPError):
                        report["cleanup"].append({"document_id": document_id, "deleted": False})
    report["failure"] = failure
    report["passed"] = sum(case["score"]["passed"] for case in report["cases"])
    report["total"] = len(cases)
    path = private_report(report, args.report_dir)
    cleanup_failed = sum(not item["deleted"] for item in report["cleanup"])
    outcome = (
        "INCONCLUSIVE"
        if failure or cleanup_failed
        else "PASS"
        if report["passed"] == len(cases)
        else "FAIL"
    )
    print(
        f"report={path} result={outcome} completed={len(report['cases'])}/{len(cases)} "
        f"pass={report['passed']} cleanup_failed={cleanup_failed}"
    )
    if failure:
        print(f"run_failed={failure}")
    if failure or not all(item["deleted"] for item in report["cleanup"]):
        return 2
    return 0 if report["passed"] == len(cases) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-dir", type=Path, default=Path.home() / "NTU/CA6000/课件")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "evals/local")
    parser.add_argument("--document", action="append", help="Run only a named manifest document")
    parser.add_argument("--retry-provider-limit", action="store_true")
    parser.add_argument("--limit-wait-seconds", type=int, default=65)
    parser.add_argument(
        "--live", action="store_true", help="Upload and send selected lecture text to providers"
    )
    parser.add_argument("--keep-documents", action="store_true")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except EvaluationError as exc:
        print(f"evaluation_error={exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
