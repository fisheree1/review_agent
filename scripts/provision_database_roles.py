from __future__ import annotations

import asyncio

import asyncpg  # type: ignore[import-untyped]

from app.core.config import ProvisioningSettings
from app.core.database_schema import APPLICATION_SCHEMA, AUTH_SCHEMA, MIGRATION_SCHEMA


async def _quoted_identifier(connection: asyncpg.Connection, value: str) -> str:
    quoted = await connection.fetchval("SELECT quote_ident($1)", value)
    if not isinstance(quoted, str):
        raise RuntimeError("PostgreSQL did not return a quoted identifier")
    return quoted


async def _quoted_literal(connection: asyncpg.Connection, value: str) -> str:
    quoted = await connection.fetchval("SELECT quote_literal($1)", value)
    if not isinstance(quoted, str):
        raise RuntimeError("PostgreSQL did not return a quoted literal")
    return quoted


async def _ensure_login_role(
    connection: asyncpg.Connection,
    *,
    role_name: str,
    password: str,
) -> str:
    role_identifier = await _quoted_identifier(connection, role_name)
    password_literal = await _quoted_literal(connection, password)
    role_exists = await connection.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role_name
    )
    role_options = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"

    if role_exists:
        await connection.execute(
            f"ALTER ROLE {role_identifier} WITH {role_options} PASSWORD {password_literal}"
        )
    else:
        await connection.execute(
            f"CREATE ROLE {role_identifier} WITH {role_options} PASSWORD {password_literal}"
        )

    return role_identifier


async def provision_database_roles() -> None:
    settings = ProvisioningSettings()  # type: ignore[call-arg]
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_admin_user,
        password=settings.postgres_admin_password.get_secret_value(),
    )

    try:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(728451204)")

            migrator = await _ensure_login_role(
                connection,
                role_name=settings.postgres_migrator_user,
                password=settings.postgres_migrator_password.get_secret_value(),
            )
            runtime = await _ensure_login_role(
                connection,
                role_name=settings.postgres_runtime_user,
                password=settings.postgres_runtime_password.get_secret_value(),
            )
            database = await _quoted_identifier(connection, settings.postgres_db)
            application_schema = await _quoted_identifier(connection, APPLICATION_SCHEMA)
            auth_schema = await _quoted_identifier(connection, AUTH_SCHEMA)
            migration_schema = await _quoted_identifier(connection, MIGRATION_SCHEMA)

            await connection.execute(f"REVOKE TEMPORARY ON DATABASE {database} FROM PUBLIC")
            await connection.execute(f"GRANT CONNECT ON DATABASE {database} TO {migrator}")
            await connection.execute(f"GRANT CONNECT ON DATABASE {database} TO {runtime}")
            await connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")

            await connection.execute(
                f"CREATE SCHEMA IF NOT EXISTS {application_schema} AUTHORIZATION {migrator}"
            )
            await connection.execute(f"ALTER SCHEMA {application_schema} OWNER TO {migrator}")
            await connection.execute(
                f"CREATE SCHEMA IF NOT EXISTS {auth_schema} AUTHORIZATION {migrator}"
            )
            await connection.execute(f"ALTER SCHEMA {auth_schema} OWNER TO {migrator}")
            await connection.execute(
                f"CREATE SCHEMA IF NOT EXISTS {migration_schema} AUTHORIZATION {migrator}"
            )
            await connection.execute(f"ALTER SCHEMA {migration_schema} OWNER TO {migrator}")

            await connection.execute(f"REVOKE ALL ON SCHEMA {application_schema} FROM PUBLIC")
            await connection.execute(f"REVOKE ALL ON SCHEMA {auth_schema} FROM PUBLIC")
            await connection.execute(f"REVOKE ALL ON SCHEMA {migration_schema} FROM PUBLIC")
            await connection.execute(f"GRANT USAGE ON SCHEMA {application_schema} TO {runtime}")
            await connection.execute(f"GRANT USAGE ON SCHEMA {auth_schema} TO {runtime}")
            await connection.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                f"IN SCHEMA {application_schema} TO {runtime}"
            )
            await connection.execute(
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {application_schema} TO {runtime}"
            )
            await connection.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                f"IN SCHEMA {auth_schema} TO {runtime}"
            )
            await connection.execute(
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {auth_schema} TO {runtime}"
            )
            await connection.execute(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator} "
                f"IN SCHEMA {application_schema} "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {runtime}"
            )
            await connection.execute(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator} "
                f"IN SCHEMA {application_schema} "
                f"GRANT USAGE, SELECT ON SEQUENCES TO {runtime}"
            )
            await connection.execute(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator} "
                f"IN SCHEMA {auth_schema} "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {runtime}"
            )
            await connection.execute(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator} "
                f"IN SCHEMA {auth_schema} "
                f"GRANT USAGE, SELECT ON SEQUENCES TO {runtime}"
            )
            await connection.execute(
                f"ALTER ROLE {migrator} IN DATABASE {database} "
                f"SET search_path = {application_schema}, pg_catalog, public"
            )
            await connection.execute(
                f"ALTER ROLE {runtime} IN DATABASE {database} "
                f"SET search_path = {application_schema}, pg_catalog, public"
            )
    finally:
        await connection.close()


def main() -> None:
    asyncio.run(provision_database_roles())
    print("Database roles and schemas are ready.")


if __name__ == "__main__":
    main()
