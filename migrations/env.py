from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.auth import models as auth_models
from app.core.config import MigrationSettings
from app.core.database_schema import APPLICATION_SCHEMA, MIGRATION_SCHEMA
from app.core.models import Base
from app.documents.infrastructure import models as document_models
from app.jobs import models as job_models
from app.learning import models as learning_models
from app.rag import models as rag_models

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
assert document_models.WorkspaceModel.__table__.schema == APPLICATION_SCHEMA
assert job_models.WorkerHeartbeatModel.__table__.schema == APPLICATION_SCHEMA
assert rag_models.DocumentIndex.__table__.schema == APPLICATION_SCHEMA
assert learning_models.Quiz.__table__.schema == APPLICATION_SCHEMA
assert auth_models.AuthSession.__table__.schema == "review_agent_auth"


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
    # Keep PostgreSQL reflection from eliding the application schema on foreign keys.
    # The role default includes review_agent, so Alembic would otherwise treat it as
    # an unqualified default schema and report false foreign-key drift.
    connection.execute(text("SET search_path TO public, pg_catalog"))
    connection.commit()
    connection.dialect.default_schema_name = "public"
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
