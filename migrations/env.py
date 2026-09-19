from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import MetaData, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import MigrationSettings
from app.core.database_schema import APPLICATION_SCHEMA, MIGRATION_SCHEMA

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = MetaData(schema=APPLICATION_SCHEMA)


def _migration_settings() -> MigrationSettings:
    # BaseSettings supplies required secrets from environment sources at runtime.
    return MigrationSettings()  # type: ignore[call-arg]


def run_migrations_offline() -> None:
    context.configure(
        url=_migration_settings().sqlalchemy_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=MIGRATION_SCHEMA,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table_schema=MIGRATION_SCHEMA,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _migration_settings().sqlalchemy_database_url
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
