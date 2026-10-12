"""Evaluate a private, versioned PDF gold set through the real conversation API.

Default mode only validates the gold set and PDF. --live uploads/indexes one PDF
and runs at most 20 cases using the configured Worker and model providers.
Reports and gold answers must remain under the gitignored evals/local directory.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pypdf import PdfReader
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.documents.infrastructure.models import DocumentVersionModel, WorkspaceModel
from app.learning.models import ConversationMessage
from app.learning.run_models import AgentRun, AgentStageExecution
from app.learning.scope import retrieve_sources
from app.rag.domain import CHUNK_VERSION, RETRIEVAL_VERSION
from app.rag.models import DocumentChunk, DocumentIndex
from scripts.evaluate_ca6000_rag import EvaluationError, api_json, source_hash, wait_for_status

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_ROOT = ROOT / "evals/local"
MESSAGE_TERMINAL = {"answered", "insufficient", "failed", "cancelled"}
RUN_STOPPED = {"completed", "failed", "cancelled", "expired", "blocked", "waiting_input"}


def application_fingerprint(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "app").rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\x00")
        digest.update(path.read_bytes() + b"\x00")
    return digest.hexdigest()


class GoldCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    category: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=2, max_length=2000)
    setup_question: str | None = Field(default=None, min_length=2, max_length=2000)
    expected: Literal["answered", "insufficient"] = "answered"
    source_page_groups: list[list[int]] = Field(default_factory=list, max_length=26)
    concept_groups: list[list[str]] = Field(default_factory=list, max_length=30)
    reference_answer: str = Field(min_length=2, max_length=4000)
    rubric: list[str] = Field(min_length=1, max_length=15)
    visual_pages: list[int] = Field(default_factory=list)


class GoldSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["pdf-agent-eval-v1"]
    version: str = Field(min_length=1, max_length=120)
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1, le=500)
    cases: list[GoldCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_gold(self) -> GoldSet:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case IDs")
        for case in self.cases:
            pages = [page for group in case.source_page_groups for page in group]
            pages += case.visual_pages
            if any(page < 1 or page > self.page_count for page in pages):
                raise ValueError("Gold page outside PDF")
            if any(not group for group in case.source_page_groups):
                raise ValueError("Empty page alternative group")
            if any(
                not group or any(not term.strip() for term in group)
                for group in case.concept_groups
            ):
                raise ValueError("Empty concept alternative group")
            if case.expected == "answered" and not case.source_page_groups:
                raise ValueError("Answered case requires expected source pages")
            if case.expected == "insufficient" and case.source_page_groups:
                raise ValueError("Insufficient case must not require source pages")
        return self


def private_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(PRIVATE_ROOT.resolve()):
        raise EvaluationError("Gold sets and reports must be inside evals/local/")
    return resolved


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def page_coverage(groups: list[list[int]], pages: set[int]) -> float | None:
    return (
        sum(bool(pages.intersection(group)) for group in groups) / len(groups) if groups else None
    )


def score_answer(
    case: GoldCase,
    message: dict[str, Any],
    scope: list[dict[str, Any]],
    contents: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Screen source integrity and concepts; semantic correctness needs human review."""
    errors: list[str] = []
    answer = message.get("answer") or {}
    claims = answer.get("claims") or []
    if case.expected == "insufficient":
        if (
            message.get("status") != "insufficient"
            or answer.get("insufficient_evidence") is not True
        ):
            errors.append("expected_insufficient_evidence")
        if claims:
            errors.append("unsupported_claims_on_missing_evidence")
        return {"screen_passed": not errors, "errors": errors, "cited_pages": []}
    if message.get("status") != "answered" or answer.get("insufficient_evidence") is not False:
        errors.append("expected_answer")
    if not claims:
        errors.append("missing_knowledge_points")
    allowed = {(str(item["document_id"]), item["version_id"]) for item in scope}
    pages: set[int] = set()
    citation_count = 0
    invalid_citations = 0
    for claim in claims:
        if not claim.get("title") or not claim.get("explanation"):
            errors.append("missing_point_title_or_explanation")
        citations = claim.get("citations") or []
        if not citations:
            errors.append("uncited_knowledge_point")
        for citation in citations:
            citation_count += 1
            valid = True
            if (str(citation.get("document_id")), citation.get("version_id")) not in allowed:
                errors.append("citation_outside_selected_version")
                valid = False
            try:
                UUID(str(citation.get("source_id")))
            except ValueError:
                errors.append("invalid_source_id")
                valid = False
            page = citation.get("unit")
            source = contents.get(page)
            if not source:
                errors.append("citation_page_unavailable")
                valid = False
            else:
                if citation.get("locator") != source.get("citation_locator"):
                    errors.append("citation_locator_mismatch")
                    valid = False
                quote = citation.get("quote")
                if (
                    not isinstance(quote, str)
                    or not quote
                    or quote not in source.get("content", "")
                ):
                    errors.append("citation_quote_not_in_source")
                    valid = False
            if valid:
                pages.add(page)
            else:
                invalid_citations += 1
    coverage = page_coverage(case.source_page_groups, pages)
    if coverage != 1:
        errors.append("expected_page_group_not_cited")
    text = (
        " ".join(
            str(claim.get(field) or "")
            for claim in claims
            for field in ("title", "text", "explanation")
        )
        + " "
        + str(answer.get("explanation") or "")
    )
    missing = [
        index + 1
        for index, alternatives in enumerate(case.concept_groups)
        if not any(term.casefold() in text.casefold() for term in alternatives)
    ]
    if missing:
        errors.append("concept_screen_missing_terms")
    return {
        "screen_passed": not errors,
        "errors": sorted(set(errors)),
        "cited_pages": sorted(pages),
        "expected_page_group_coverage": coverage,
        "citation_count": citation_count,
        "invalid_citations": invalid_citations,
        "missing_concept_groups": missing,
        "semantic_review": "pending",
    }


async def observe_run(run_id: str, prior_question: str | None) -> dict[str, Any]:
    """Read owned receipts and replay scoped retrieval using the saved query vector.

    This is replay Recall@8, not a trace of the original model context. No new
    embedding call is made. Connect only to the evaluated API's local database.
    """
    principal = get_settings().local_workspace_id
    if principal is None:
        raise EvaluationError("Database observation needs LOCAL_WORKSPACE_ID")
    async with async_session_factory() as session:
        workspace = await session.scalar(
            select(WorkspaceModel.id).where(WorkspaceModel.public_id == principal)
        )
        run = await session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace, AgentRun.public_id == UUID(run_id)
            )
        )
        if run is None:
            raise EvaluationError("Run missing in the evaluated local database")
        message = await session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.workspace_id == workspace,
                ConversationMessage.id == run.message_id,
            )
        )
        if message is None:
            raise EvaluationError("Run message unavailable")
        calls = list(
            await session.scalars(
                select(AgentStageExecution)
                .where(
                    AgentStageExecution.workspace_id == workspace,
                    AgentStageExecution.run_id == run.id,
                )
                .order_by(AgentStageExecution.id)
            )
        )
        result: dict[str, Any] = {
            "profile": run.profile,
            "graph_version": run.graph_version,
            "usage": run.usage,
            "answer_usage": message.usage,
            "calls": [
                {"kind": call.kind, "stage": call.stage, "usage": call.usage} for call in calls
            ],
            "retrieval_replay": None,
            "retrieval_unavailable_reason": "query_receipt_unavailable_or_already_cleared",
        }
        versions = [item["version_id"] for item in run.scope]
        parsed = list(
            await session.scalars(
                select(DocumentVersionModel).where(
                    DocumentVersionModel.workspace_id == workspace,
                    DocumentVersionModel.id.in_(versions),
                )
            )
        )
        result["parsers"] = [
            {
                "version_id": version.id,
                "name": version.parser_name,
                "version": version.parser_version,
            }
            for version in parsed
        ]
        chunks = list(
            await session.scalars(
                select(DocumentChunk)
                .join(
                    DocumentIndex,
                    (DocumentChunk.index_id == DocumentIndex.id)
                    & (DocumentChunk.workspace_id == DocumentIndex.workspace_id),
                )
                .where(
                    DocumentChunk.workspace_id == workspace,
                    DocumentIndex.document_version_id.in_(versions),
                    DocumentIndex.profile == run.profile,
                    DocumentIndex.status == "ready",
                )
            )
        )
        by_id = {str(chunk.public_id): chunk for chunk in chunks}
        invalid_source_ids: list[str] = []
        for claim in (message.answer or {}).get("claims", []):
            for citation in claim.get("citations", []):
                chunk = by_id.get(citation.get("source_id"))
                if (
                    chunk is None
                    or citation.get("unit") != chunk.unit
                    or not citation.get("quote")
                    or citation["quote"] not in chunk.content
                ):
                    invalid_source_ids.append(str(citation.get("source_id")))
        result["invalid_citation_chunks"] = invalid_source_ids
        if (run.plan or {}).get("summary_mode") == "overview":
            result["retrieval_unavailable_reason"] = "overview_uses_batched_reading"
            return result
        embed = next((call for call in calls if call.kind == "embed" and call.result), None)
        if embed is not None and isinstance(embed.result, list):
            question = (run.plan or {}).get("summary_request") or message.question
            query = " ".join([prior_question, question])[:3000] if prior_question else question
            sources = await retrieve_sources(
                session, run.workspace_id, run.scope, run.profile, embed.result[0], query
            )
            result["retrieval_replay"] = [
                {
                    "source_id": str(source.id),
                    "page": source.unit,
                    "document_id": str(source.document_id),
                    "version_id": source.version_id,
                    "similarity": source.similarity,
                }
                for source in sources
            ]
            result["retrieval_unavailable_reason"] = None
        return result


def merge_observation(final: dict[str, Any], captured: dict[str, Any] | None) -> dict[str, Any]:
    """Keep metadata captured before publication clears private call receipts."""
    if captured and captured.get("retrieval_replay") is not None:
        return {
            **final,
            "retrieval_replay": captured["retrieval_replay"],
            "retrieval_unavailable_reason": None,
            "retrieval_capture": "during_active_run",
        }
    return final


async def ask_case(
    client: httpx.AsyncClient,
    conversation_id: str,
    question: str,
    key: str,
    timeout_seconds: int,
    *,
    observe_db: bool = False,
    prior_question: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, float, dict[str, Any] | None]:
    started = time.monotonic()
    posted = await api_json(
        client,
        "POST",
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": key},
        json={"question": question},
    )
    deadline = time.monotonic() + timeout_seconds
    run = None
    captured = None
    while time.monotonic() < deadline:
        detail = await api_json(client, "GET", f"/api/v1/conversations/{conversation_id}")
        message = next(item for item in detail["messages"] if item["id"] == posted["id"])
        if message.get("run_id"):
            run = await api_json(client, "GET", f"/api/v1/agent-runs/{message['run_id']}")
        # The two GETs are separate snapshots. Publication can commit between
        # them, so a completed run requires a fresh terminal message snapshot.
        stopped = run and run["status"] in RUN_STOPPED and run["status"] != "completed"
        if message["status"] in MESSAGE_TERMINAL or stopped:
            observed = (
                merge_observation(await observe_run(message["run_id"], prior_question), captured)
                if observe_db and message.get("run_id")
                else None
            )
            return message, run, round(time.monotonic() - started, 2), observed
        if (
            observe_db
            and run
            and run["stage"] == "summary"
            and (run.get("plan") or {}).get("summary_mode") != "overview"
            and captured is None
        ):
            snapshot = await observe_run(message["run_id"], prior_question)
            if snapshot["retrieval_replay"] is not None:
                captured = snapshot
        await asyncio.sleep(1)
    # Bound outstanding work before a timeout is reported; never resend automatically.
    await api_json(
        client, "POST", f"/api/v1/conversations/{conversation_id}/messages/{posted['id']}:cancel"
    )
    raise EvaluationError("Agent case timed out and was cancelled")


async def evaluate_case(
    client: httpx.AsyncClient,
    case: GoldCase,
    document_id: str,
    run_id: str,
    timeout_seconds: int,
    observe_db: bool,
) -> dict[str, Any]:
    conversation = await api_json(
        client,
        "POST",
        "/api/v1/conversations",
        headers={"Idempotency-Key": f"pdf-eval-{run_id}-{case.id}-conversation"},
        json={"title": f"PDF eval {case.id}", "document_ids": [document_id]},
    )
    setup = None
    setup_run = None
    setup_duration = None
    setup_observation = None
    if case.setup_question:
        setup, setup_run, setup_duration, setup_observation = await ask_case(
            client,
            conversation["id"],
            case.setup_question,
            f"pdf-eval-{run_id}-{case.id}-setup",
            timeout_seconds,
            observe_db=observe_db,
        )
    message, run, duration, observed = await ask_case(
        client,
        conversation["id"],
        case.question,
        f"pdf-eval-{run_id}-{case.id}",
        timeout_seconds,
        observe_db=observe_db,
        prior_question=case.setup_question if setup and setup["status"] == "answered" else None,
    )
    contents: dict[int, dict[str, Any]] = {}
    version = conversation["scope"][0]["version_id"]
    for claim in (message.get("answer") or {}).get("claims") or []:
        for citation in claim.get("citations") or []:
            page = citation.get("unit")
            if isinstance(page, int) and 1 <= page <= 500 and page not in contents:
                source = await api_json(
                    client,
                    "GET",
                    f"/api/v1/documents/{document_id}/content",
                    params={"ordinal": page, "version_id": version},
                )
                contents[page] = next(
                    (item for item in source["contents"] if item["ordinal"] == page), {}
                )
    result = {
        "id": case.id,
        "category": case.category,
        "status": message["status"],
        "duration_seconds": duration,
        "scope": conversation["scope"],
        "conversation_id": conversation["id"],
        "run_id": message.get("run_id"),
        "failure_code": message.get("failure_code"),
        "gold": case.model_dump(),
        "answer_for_manual_review": message.get("answer"),
        "setup_for_manual_review": setup,
        "setup_run": setup_run,
        "setup_duration_seconds": setup_duration,
        "setup_observation": setup_observation,
        "run": run,
        "score": score_answer(case, message, conversation["scope"], contents),
    }
    if observed is not None:
        replay = observed["retrieval_replay"]
        observed["expected_page_group_recall_at_8"] = (
            page_coverage(case.source_page_groups, {source["page"] for source in replay})
            if replay is not None
            else None
        )
        result["observation"] = observed
        if observed["invalid_citation_chunks"]:
            result["score"]["screen_passed"] = False
            result["score"]["errors"].append("citation_chunk_not_in_scoped_index")
    if setup and setup["status"] != "answered":
        result["score"]["screen_passed"] = False
        result["score"]["errors"].append("follow_up_setup_did_not_answer")
    return result


def select_cases(gold: GoldSet, selected: list[str] | None, limit: int) -> list[GoldCase]:
    if not 1 <= limit <= 20:
        raise EvaluationError("Case limit must be 1–20")
    if selected and (
        len(selected) != len(set(selected)) or set(selected) - {case.id for case in gold.cases}
    ):
        raise EvaluationError("Unknown or duplicate case selection")
    cases = [case for case in gold.cases if not selected or case.id in selected]
    if len(cases) > limit:
        raise EvaluationError(
            "Selected cases exceed --case-limit; select --case or raise limit to at most 20"
        )
    return cases


async def run(args: argparse.Namespace) -> int:
    try:
        gold = GoldSet.model_validate_json(private_path(args.manifest).read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise EvaluationError("Invalid or unreadable private gold set") from exc
    if not args.pdf.is_file() or args.pdf.stat().st_size > 25 * 1024 * 1024:
        raise EvaluationError("PDF missing or larger than 25 MiB")
    if (
        source_hash(args.pdf) != gold.pdf_sha256
        or len(PdfReader(args.pdf).pages) != gold.page_count
    ):
        raise EvaluationError("PDF hash/page count differs from the gold set")
    cases = select_cases(gold, args.case, args.case_limit)
    output_dir = private_path(args.report_dir)
    parsed = urlparse(args.base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
    ):
        raise EvaluationError("Only a local HTTP API is allowed")
    if not 30 <= args.timeout_seconds <= 1800:
        raise EvaluationError("Timeout must be 30–1800 seconds")
    print(
        f"gold={gold.version} pages={gold.page_count} cases={len(cases)} hash_verified=true",
        flush=True,
    )
    if not args.live:
        return 0
    token = get_settings().local_api_token.get_secret_value()
    if not token:
        raise EvaluationError("LOCAL_API_TOKEN is not configured")
    git = await asyncio.create_subprocess_exec(
        "git",
        "rev-parse",
        "HEAD",
        cwd=ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    git_output, _ = await git.communicate()
    report: dict[str, Any] = {
        "schema_version": gold.schema_version,
        "version": gold.version,
        "run_id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "pdf_sha256": gold.pdf_sha256,
        "gold_sha256": source_hash(args.manifest),
        "page_count": gold.page_count,
        "git_head": git_output.decode().strip(),
        "application_sha256": application_fingerprint(),
        "chunk_version": CHUNK_VERSION,
        "retrieval_version": RETRIEVAL_VERSION,
        "cases": [],
        "cleanup": [],
        "failure": None,
        "selected_case_ids": [case.id for case in cases],
        "semantic_review": "pending; automated screening is not semantic accuracy",
    }
    document_id = None
    created = False
    async with httpx.AsyncClient(
        base_url=args.base_url, headers={"Authorization": f"Bearer {token}"}, timeout=60
    ) as client:
        try:
            openapi = await api_json(client, "GET", "/openapi.json")
            if "/api/v1/agent-runs/{run_id}" not in openapi.get("paths", {}):
                raise EvaluationError("Current graph Agent API is not available")
            started = time.monotonic()
            with args.pdf.open("rb") as source:
                upload = await api_json(
                    client,
                    "POST",
                    "/api/v1/documents",
                    headers={"Idempotency-Key": f"pdf-eval-{report['run_id']}-upload"},
                    files={"file": (args.pdf.name, source, "application/pdf")},
                )
            document_id, created = upload["document"]["id"], not upload["deduplicated"]
            report["document_id"] = document_id
            state = await wait_for_status(
                client,
                f"/api/v1/documents/{document_id}",
                {"ready", "failed"},
                args.timeout_seconds,
            )
            if state["status"] != "ready":
                raise EvaluationError(f"PDF parsing failed: {state.get('failure_code')}")
            report["parse_seconds"] = round(time.monotonic() - started, 2)
            started = time.monotonic()
            await api_json(
                client,
                "POST",
                f"/api/v1/documents/{document_id}/index",
                headers={"Idempotency-Key": f"pdf-eval-{report['run_id']}-index"},
            )
            state = await wait_for_status(
                client,
                f"/api/v1/documents/{document_id}/index",
                {"ready", "failed"},
                args.timeout_seconds,
            )
            report["index_seconds"] = round(time.monotonic() - started, 2)
            report["index"] = state
            if state["status"] != "ready":
                raise EvaluationError(f"PDF indexing failed: {state.get('failure_code')}")
            print("pdf_parsed=true index_ready=true", flush=True)
            for case in cases:
                result = await evaluate_case(
                    client,
                    case,
                    document_id,
                    report["run_id"],
                    args.timeout_seconds,
                    args.observe_db,
                )
                report["cases"].append(result)
                print(
                    f"case={case.id} status={result['status']} "
                    f"screen={'PASS' if result['score']['screen_passed'] else 'FAIL'} "
                    f"seconds={result['duration_seconds']}",
                    flush=True,
                )
                # Save partial results even if the next case or process fails.
                write_private_json(output_dir / f"{report['run_id']}-{case.id}.json", result)
        except EvaluationError as exc:
            report["failure"] = str(exc)
        except Exception as exc:
            # Top-level boundary saves partial work and cleanup, without payload logs.
            report["failure"] = f"UNEXPECTED_{type(exc).__name__}"
        finally:
            if created and document_id and not args.keep_document:
                try:
                    await api_json(client, "DELETE", f"/api/v1/documents/{document_id}")
                    deleted = (
                        await client.get(f"/api/v1/documents/{document_id}")
                    ).status_code == 404
                    report["cleanup"].append({"document_id": document_id, "deleted": deleted})
                except (EvaluationError, httpx.HTTPError):
                    report["cleanup"].append({"document_id": document_id, "deleted": False})
            report["document_retained"] = bool(document_id and (not created or args.keep_document))
    report["screen_passed"] = sum(case["score"]["screen_passed"] for case in report["cases"])
    report["completed"] = len(report["cases"])
    report["total"] = len(cases)
    infrastructure_failed = (
        bool(report["failure"])
        or any(
            case["status"] in {"failed", "cancelled", "processing", "queued"}
            for case in report["cases"]
        )
        or any(not item["deleted"] for item in report["cleanup"])
    )
    report["outcome"] = (
        "INCONCLUSIVE"
        if infrastructure_failed
        else "SCREEN_PASS"
        if report["screen_passed"] == len(cases)
        else "SCREEN_FAIL"
    )
    path = output_dir / f"{report['run_id']}-report.json"
    write_private_json(path, report)
    print(
        f"report={path} result={report['outcome']} "
        f"completed={report['completed']}/{len(cases)} screen_pass={report['screen_passed']}",
        flush=True,
    )
    if report["failure"]:
        print(f"run_failure={report['failure']}", flush=True)
    return 2 if infrastructure_failed else 0 if report["screen_passed"] == len(cases) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--report-dir", type=Path, default=PRIVATE_ROOT / "reports")
    parser.add_argument("--case", action="append")
    parser.add_argument("--case-limit", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--observe-db",
        action="store_true",
        help="Read owned receipts and replay Recall@8 in the matching local DB",
    )
    parser.add_argument("--keep-document", action="store_true")
    try:
        return asyncio.run(run(parser.parse_args()))
    except EvaluationError as exc:
        print(f"evaluation_error={exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
