"""Verify the Quiz audit migration on an explicitly isolated, empty database only."""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import MigrationSettings
from app.core.database_schema import APPLICATION_SCHEMA as S
from app.core.database_schema import MIGRATION_SCHEMA
from scripts.provision_database_roles import main as provision


async def assert_empty(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            version_table = await connection.scalar(
                text("SELECT to_regclass(:name)"), {"name": f"{MIGRATION_SCHEMA}.alembic_version"}
            )
            if version_table is not None:
                raise RuntimeError(
                    "Requires an empty isolated database; no downgrade was attempted"
                )
    finally:
        await engine.dispose()


async def seed_previous(database_url: str, workspace: UUID, quiz: UUID, message: UUID) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            owner = await connection.scalar(
                text(f"""
                INSERT INTO {S}.workspaces (public_id, name, status)
                VALUES (:id, 'migration fixture', 'active') RETURNING id
            """),
                {"id": workspace},
            )
            await connection.execute(
                text(f"""
                INSERT INTO {S}.quizzes
                (public_id, workspace_id, idempotency_key, title, config,
                 requested_scope, scope, profile, status)
                VALUES (:id, :workspace, 'migration-fixture', 'Existing quiz',
                        '{{"topic":"statistics"}}'::jsonb, '{{}}'::jsonb,
                        '[]'::jsonb, 'migration-fixture', 'ready')
            """),
                {"id": quiz, "workspace": owner},
            )
            conversation = await connection.scalar(
                text(f"""
                INSERT INTO {S}.conversations (public_id, workspace_id, title, scope, created_at)
                VALUES (:id, :workspace, 'Existing conversation', '[]'::jsonb, now()) RETURNING id
            """),
                {"id": uuid4(), "workspace": owner},
            )
            await connection.execute(
                text(f"""
                INSERT INTO {S}.conversation_messages
                (public_id, workspace_id, conversation_id, idempotency_key,
                 question, scope, profile, status, answer)
                VALUES (:id, :workspace, :conversation, 'migration-message', 'Existing question',
                        '[]'::jsonb, 'migration-fixture', 'insufficient',
                        CAST(:answer AS jsonb))
            """),
                {
                    "id": message,
                    "workspace": owner,
                    "conversation": conversation,
                    "answer": json.dumps({"insufficient_evidence": True, "claims": []}),
                },
            )
    finally:
        await engine.dispose()


async def verify_preservation(
    database_url: str, workspace: UUID, quiz: UUID, message: UUID
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            row = (
                await connection.execute(
                    text(f"""
                SELECT title, config, generation_usage FROM {S}.quizzes WHERE public_id = :id
            """),
                    {"id": quiz},
                )
            ).one()
            assert row.title == "Existing quiz" and row.config == {"topic": "statistics"}
            assert row.generation_usage is None
            existing = (
                await connection.execute(
                    text(f"""
                SELECT question, answer, task_result, quiz_id FROM {S}.conversation_messages
                WHERE public_id = :id
            """),
                    {"id": message},
                )
            ).one()
            assert existing.question == "Existing question"
            assert existing.answer == {"insufficient_evidence": True, "claims": []}
            assert existing.task_result is None and existing.quiz_id is None
            await connection.execute(
                text(f"DELETE FROM {S}.workspaces WHERE public_id = :id"), {"id": workspace}
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated", required=True, action="store_true")
    parser.add_argument(
        "--previous-revision",
        choices=["0011_version_purge_trigger", "0012_quiz_agent_usage"],
        default="0012_quiz_agent_usage",
    )
    args = parser.parse_args()
    provision()
    # BaseSettings loads the required password from environment sources.
    database_url = MigrationSettings().sqlalchemy_database_url  # type: ignore[call-arg]
    asyncio.run(assert_empty(database_url))
    config = Config("alembic.ini")
    command.upgrade(config, args.previous_revision)
    workspace, quiz, message = uuid4(), uuid4(), uuid4()
    asyncio.run(seed_previous(database_url, workspace, quiz, message))
    command.upgrade(config, "head")
    asyncio.run(verify_preservation(database_url, workspace, quiz, message))
    command.check(config)
    print(
        "PASS: prior-schema Quiz and conversation preserved; "
        "nullable task results added; metadata matches head"
    )


if __name__ == "__main__":
    main()
