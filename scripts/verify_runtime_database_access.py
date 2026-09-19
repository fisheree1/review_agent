from __future__ import annotations

import asyncio

import asyncpg  # type: ignore[import-untyped]

from app.core.config import Settings
from app.core.database_schema import APPLICATION_SCHEMA


async def _assert_ddl_denied(
    connection: asyncpg.Connection,
    *,
    statement: str,
    cleanup_statement: str,
) -> None:
    try:
        await connection.execute(statement)
    except asyncpg.InsufficientPrivilegeError:
        return
    await connection.execute(cleanup_statement)
    raise RuntimeError(f"Runtime database role unexpectedly executed DDL: {statement}")


async def verify_runtime_database_access() -> None:
    settings = Settings()  # type: ignore[call-arg]
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_runtime_user,
        password=settings.postgres_runtime_password.get_secret_value(),
    )

    try:
        role = await connection.fetchrow(
            "SELECT current_user AS role_name, rolsuper, rolcreatedb, rolcreaterole, "
            "rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if role is None:
            raise RuntimeError("Runtime database role was not found")
        if role["role_name"] != settings.postgres_runtime_user:
            raise RuntimeError("Application connected with an unexpected database role")
        administrative_fields = ("rolsuper", "rolcreatedb", "rolcreaterole", "rolbypassrls")
        if any(role[field] for field in administrative_fields):
            raise RuntimeError("Runtime database role has administrative privileges")

        await _assert_ddl_denied(
            connection,
            statement=f"CREATE TABLE {APPLICATION_SCHEMA}.__runtime_ddl_probe (id integer)",
            cleanup_statement=f"DROP TABLE {APPLICATION_SCHEMA}.__runtime_ddl_probe",
        )
        await _assert_ddl_denied(
            connection,
            statement="CREATE TEMPORARY TABLE __runtime_temp_ddl_probe (id integer)",
            cleanup_statement="DROP TABLE __runtime_temp_ddl_probe",
        )
    finally:
        await connection.close()


def main() -> None:
    asyncio.run(verify_runtime_database_access())
    print("Runtime database role is non-administrative and DDL is denied.")


if __name__ == "__main__":
    main()
