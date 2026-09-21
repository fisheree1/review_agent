from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]

from app.core.config import MigrationSettings
from app.core.database_schema import APPLICATION_SCHEMA


async def verify_document_tenant_constraints() -> None:
    settings = MigrationSettings()  # type: ignore[call-arg]
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_migrator_user,
        password=settings.postgres_migrator_password.get_secret_value(),
    )
    transaction = connection.transaction()
    await transaction.start()
    try:
        workspace_a = await connection.fetchval(
            f"INSERT INTO {APPLICATION_SCHEMA}.workspaces (public_id, name, status) "
            "VALUES ($1, $2, 'active') RETURNING id",
            uuid4(),
            "Constraint probe A",
        )
        workspace_b = await connection.fetchval(
            f"INSERT INTO {APPLICATION_SCHEMA}.workspaces (public_id, name, status) "
            "VALUES ($1, $2, 'active') RETURNING id",
            uuid4(),
            "Constraint probe B",
        )
        document_id = await connection.fetchval(
            f"""
            INSERT INTO {APPLICATION_SCHEMA}.documents (
                public_id, workspace_id, original_filename, media_type, byte_size,
                sha256, object_key, upload_idempotency_key, status
            ) VALUES ($1, $2, 'constraint-probe.pdf', 'application/pdf', 1,
                      $3, $4, $5, 'uploaded')
            RETURNING id
            """,
            uuid4(),
            workspace_a,
            "a" * 64,
            f"constraint-probe/{uuid4()}",
            str(uuid4()),
        )

        savepoint = connection.transaction()
        await savepoint.start()
        try:
            await connection.execute(
                f"""
                INSERT INTO {APPLICATION_SCHEMA}.document_versions (
                    document_id, workspace_id, version_no, source_sha256,
                    parser_name, parser_version, status, page_count
                ) VALUES ($1, $2, 1, $3, 'probe', '1', 'ready', 0)
                """,
                document_id,
                workspace_b,
                "a" * 64,
            )
        except asyncpg.ForeignKeyViolationError:
            await savepoint.rollback()
        else:
            await savepoint.rollback()
            raise RuntimeError("A document version accepted a mismatched workspace")

        await connection.execute(
            f"""
            INSERT INTO {APPLICATION_SCHEMA}.document_versions (
                document_id, workspace_id, version_no, source_sha256,
                parser_name, parser_version, status, page_count
            ) VALUES ($1, $2, 1, $3, 'probe', '1', 'ready', 0)
            """,
            document_id,
            workspace_a,
            "a" * 64,
        )
    finally:
        await transaction.rollback()
        await connection.close()


def main() -> None:
    asyncio.run(verify_document_tenant_constraints())
    print("Document version workspace constraints accept valid rows and reject cross-tenant rows.")


if __name__ == "__main__":
    main()
