from __future__ import annotations

import argparse
import asyncio
import hashlib
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import httpx
from minio import Minio
from minio.error import S3Error
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.core.config import Settings


def _write_text_pdf(path: Path, *, text: str) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}  # noqa: SLF001
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)  # noqa: SLF001
    with path.open("wb") as output:
        writer.write(output)


def _wait_for_terminal_status(
    client: httpx.Client,
    *,
    document_id: str,
    headers: dict[str, str],
    timeout_seconds: float = 90,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/documents/{document_id}", headers=headers)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        if payload["status"] in {"ready", "failed"}:
            return payload
        time.sleep(0.25)
    raise RuntimeError(f"Document {document_id} did not finish in time")


async def _failure_record_counts(
    settings: Settings,
    *,
    document_id: UUID,
    retry_key: str,
) -> tuple[int, int, int]:
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_runtime_user,
        password=settings.postgres_runtime_password.get_secret_value(),
    )
    try:
        row = await connection.fetchrow(
            """
            SELECT
                count(DISTINCT j.id) FILTER (
                    WHERE j.idempotency_key = $2
                ) AS retry_jobs,
                count(DISTINCT v.id) AS versions,
                count(DISTINCT p.id) AS pages
            FROM review_agent.documents AS d
            LEFT JOIN review_agent.processing_jobs AS j ON j.document_id = d.id
            LEFT JOIN review_agent.document_versions AS v ON v.document_id = d.id
            LEFT JOIN review_agent.document_pages AS p ON p.document_version_id = v.id
            WHERE d.public_id = $1
            """,
            document_id,
            f"parse:{document_id}:retry:{retry_key}",
        )
        if row is None:
            raise RuntimeError("Failed document disappeared before verification")
        return int(row["retry_jobs"]), int(row["versions"]), int(row["pages"])
    finally:
        await connection.close()


async def _create_foreign_workspace_document(settings: Settings) -> UUID:
    workspace_public_id = uuid4()
    document_public_id = uuid4()
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_runtime_user,
        password=settings.postgres_runtime_password.get_secret_value(),
    )
    try:
        async with connection.transaction():
            workspace_id = await connection.fetchval(
                """
                INSERT INTO review_agent.workspaces (public_id, name, status)
                VALUES ($1, 'Isolation Probe', 'active')
                RETURNING id
                """,
                workspace_public_id,
            )
            await connection.execute(
                """
                INSERT INTO review_agent.documents (
                    public_id, workspace_id, original_filename, media_type,
                    byte_size, sha256, object_key, upload_idempotency_key, status
                )
                VALUES ($1, $2, 'isolation.pdf', 'application/pdf', 5, $3, $4, $5, 'failed')
                """,
                document_public_id,
                workspace_id,
                hashlib.sha256(str(document_public_id).encode()).hexdigest(),
                f"{workspace_public_id}/{document_public_id}/source.pdf",
                f"isolation-{document_public_id}",
            )
        return document_public_id
    finally:
        await connection.close()


async def _assert_foreign_document_unchanged_and_remove(
    settings: Settings, *, document_id: UUID
) -> None:
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_runtime_user,
        password=settings.postgres_runtime_password.get_secret_value(),
    )
    try:
        async with connection.transaction():
            row = await connection.fetchrow(
                """
                SELECT d.id, d.status, d.workspace_id
                FROM review_agent.documents AS d
                WHERE d.public_id = $1
                """,
                document_id,
            )
            if row is None or row["status"] != "failed":
                raise RuntimeError("Cross-workspace delete changed a foreign document")
            await connection.execute("DELETE FROM review_agent.documents WHERE id = $1", row["id"])
            await connection.execute(
                "DELETE FROM review_agent.workspaces WHERE id = $1", row["workspace_id"]
            )
    finally:
        await connection.close()


async def _deleted_record_counts(settings: Settings, *, document_id: UUID) -> tuple[str, int, int]:
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_runtime_user,
        password=settings.postgres_runtime_password.get_secret_value(),
    )
    try:
        row = await connection.fetchrow(
            """
            SELECT
                d.status,
                count(DISTINCT v.id) AS versions,
                count(DISTINCT p.id) AS pages
            FROM review_agent.documents AS d
            LEFT JOIN review_agent.document_versions AS v ON v.document_id = d.id
            LEFT JOIN review_agent.document_pages AS p ON p.document_version_id = v.id
            WHERE d.public_id = $1
            GROUP BY d.status
            """,
            document_id,
        )
        if row is None:
            raise RuntimeError("Deleted document audit record is missing")
        return str(row["status"]), int(row["versions"]), int(row["pages"])
    finally:
        await connection.close()


def _assert_object_missing(settings: Settings, *, document_id: UUID) -> None:
    client = Minio(
        settings.storage_endpoint,
        access_key=settings.storage_access_key,
        secret_key=settings.storage_secret_key.get_secret_value(),
        secure=settings.storage_secure,
    )
    object_key = f"{settings.local_workspace_id}/{document_id}/source.pdf"
    try:
        client.stat_object(settings.storage_bucket, object_key)
    except S3Error as exc:
        if exc.code not in {"NoSuchKey", "NoSuchObject"}:
            raise
    else:
        raise RuntimeError("Deleted document source still exists in object storage")


def verify(sample_path: Path, *, base_url: str) -> None:
    settings = Settings()  # type: ignore[call-arg]
    token = settings.local_api_token.get_secret_value()
    auth_headers = {"Authorization": f"Bearer {token}"}
    sample_hash = hashlib.sha256(sample_path.read_bytes()).hexdigest()

    with httpx.Client(base_url=base_url, timeout=30) as client:
        unauthenticated = client.get(f"/api/v1/documents/{uuid4()}")
        if unauthenticated.status_code != 401:
            raise RuntimeError("Document API accepted an unauthenticated request")

        foreign_id = asyncio.run(_create_foreign_workspace_document(settings))
        try:
            foreign_read = client.get(f"/api/v1/documents/{foreign_id}", headers=auth_headers)
            if foreign_read.status_code != 404:
                raise RuntimeError("Cross-workspace document was readable")
            foreign_delete = client.delete(f"/api/v1/documents/{foreign_id}", headers=auth_headers)
            if foreign_delete.status_code != 204:
                raise RuntimeError("Cross-workspace delete did not hide resource existence")
        finally:
            asyncio.run(
                _assert_foreign_document_unchanged_and_remove(settings, document_id=foreign_id)
            )

        with sample_path.open("rb") as sample_file:
            uploaded = client.post(
                "/api/v1/documents",
                headers={**auth_headers, "Idempotency-Key": f"sample-{sample_hash[:32]}"},
                files={"file": (sample_path.name, sample_file, "application/pdf")},
            )
        uploaded.raise_for_status()
        document_id = uploaded.json()["document"]["id"]
        document = _wait_for_terminal_status(
            client,
            document_id=document_id,
            headers=auth_headers,
        )
        if document["status"] != "ready":
            raise RuntimeError(f"Sample PDF failed: {document['failure_code']}")

        pages_response = client.get(f"/api/v1/documents/{document_id}/pages", headers=auth_headers)
        pages_response.raise_for_status()
        pages = pages_response.json()["pages"]
        if len(pages) != document["page_count"] or not pages:
            raise RuntimeError("Page count does not match the extracted page records")
        if pages[0]["page_number"] != 1 or not pages[0]["content"].strip():
            raise RuntimeError("The first page is missing its number or extractable text")

        with sample_path.open("rb") as sample_file:
            duplicate = client.post(
                "/api/v1/documents",
                headers={**auth_headers, "Idempotency-Key": f"duplicate-{uuid4()}"},
                files={"file": (sample_path.name, sample_file, "application/pdf")},
            )
        duplicate.raise_for_status()
        duplicate_payload = duplicate.json()
        if duplicate_payload["document"]["id"] != document_id:
            raise RuntimeError("Duplicate upload created a second document")
        if not duplicate_payload["deduplicated"]:
            raise RuntimeError("Duplicate upload was not reported as deduplicated")

        with tempfile.TemporaryDirectory(prefix="review-agent-acceptance-") as temporary_dir:
            blank_path = Path(temporary_dir) / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with blank_path.open("wb") as output:
                writer.write(output)

            upload_key = f"blank-{uuid4()}"
            with blank_path.open("rb") as blank_file:
                failed_upload = client.post(
                    "/api/v1/documents",
                    headers={**auth_headers, "Idempotency-Key": upload_key},
                    files={"file": (blank_path.name, blank_file, "application/pdf")},
                )
            failed_upload.raise_for_status()
            failed_id = failed_upload.json()["document"]["id"]
            failed_document = _wait_for_terminal_status(
                client,
                document_id=failed_id,
                headers=auth_headers,
            )
            if failed_document["failure_code"] != "PDF_TEXT_NOT_FOUND":
                raise RuntimeError("Image-only PDF did not produce the expected failure")
            if not failed_document["failure_message"]:
                raise RuntimeError("Failed processing did not include an explanation")

            retry_key = f"retry-{uuid4()}"
            retry_headers = {**auth_headers, "Idempotency-Key": retry_key}
            first_retry = client.post(f"/api/v1/documents/{failed_id}:retry", headers=retry_headers)
            first_retry.raise_for_status()
            second_retry = client.post(
                f"/api/v1/documents/{failed_id}:retry", headers=retry_headers
            )
            second_retry.raise_for_status()
            _wait_for_terminal_status(
                client,
                document_id=failed_id,
                headers=auth_headers,
            )
            job_count, version_count, page_count = asyncio.run(
                _failure_record_counts(
                    settings,
                    document_id=UUID(failed_id),
                    retry_key=retry_key,
                )
            )
            if (job_count, version_count, page_count) != (1, 0, 0):
                raise RuntimeError(
                    "Repeated retry wrote duplicate jobs, versions, or pages: "
                    f"{job_count}, {version_count}, {page_count}"
                )

            deleted = client.delete(f"/api/v1/documents/{failed_id}", headers=auth_headers)
            if deleted.status_code != 204:
                raise RuntimeError("Document deletion failed")
            missing = client.get(f"/api/v1/documents/{failed_id}", headers=auth_headers)
            if missing.status_code != 404:
                raise RuntimeError("Deleted document is still visible")

            ready_delete_path = Path(temporary_dir) / "delete-ready.pdf"
            _write_text_pdf(ready_delete_path, text="Delete this parsed document")
            with ready_delete_path.open("rb") as ready_delete_file:
                ready_delete_upload = client.post(
                    "/api/v1/documents",
                    headers={
                        **auth_headers,
                        "Idempotency-Key": f"delete-ready-{uuid4()}",
                    },
                    files={
                        "file": (
                            ready_delete_path.name,
                            ready_delete_file,
                            "application/pdf",
                        )
                    },
                )
            ready_delete_upload.raise_for_status()
            ready_delete_id = ready_delete_upload.json()["document"]["id"]
            ready_delete_document = _wait_for_terminal_status(
                client,
                document_id=ready_delete_id,
                headers=auth_headers,
            )
            if ready_delete_document["status"] != "ready":
                raise RuntimeError("Synthetic deletion document did not parse")
            ready_delete_pages = client.get(
                f"/api/v1/documents/{ready_delete_id}/pages", headers=auth_headers
            )
            ready_delete_pages.raise_for_status()
            if ready_delete_pages.json()["page_count"] != 1:
                raise RuntimeError("Synthetic deletion document did not persist its page")
            deleted_ready = client.delete(
                f"/api/v1/documents/{ready_delete_id}", headers=auth_headers
            )
            if deleted_ready.status_code != 204:
                raise RuntimeError("Ready document deletion failed")
            deletion_counts = asyncio.run(
                _deleted_record_counts(settings, document_id=UUID(ready_delete_id))
            )
            if deletion_counts != ("deleted", 0, 0):
                raise RuntimeError(
                    f"Ready document deletion left derived records: {deletion_counts}"
                )
            _assert_object_missing(settings, document_id=UUID(ready_delete_id))

    storage_scheme = "https" if settings.storage_secure else "http"
    storage_response = httpx.get(
        f"{storage_scheme}://{settings.storage_endpoint}/{settings.storage_bucket}",
        timeout=10,
    )
    if storage_response.status_code not in {401, 403}:
        raise RuntimeError("Private document bucket is anonymously accessible")

    preview = " ".join(pages[0]["content"].split())[:100]
    print(
        "Document flow verified: "
        f"id={document_id}, pages={len(pages)}, first_page_preview={preview!r}."
    )
    print(
        "Failure, idempotent retry, workspace isolation, private storage, "
        "authentication, and deletion verified."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the local PDF ingestion flow")
    parser.add_argument("sample_pdf", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()
    verify(arguments.sample_pdf.resolve(), base_url=arguments.base_url)


if __name__ == "__main__":
    main()
