"""Seed schema 0013, then verify upgrade preserves a pre-existing Quiz attempt."""

import argparse
import asyncio
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.core.config import MigrationSettings
from app.core.database import async_session_factory, close_database
from scripts.provision_database_roles import main as provision
from scripts.verify_quiz_agent_migration import assert_empty

FIXTURE_ID = UUID("8a88a321-c67c-4482-a20e-f63526b51c8b")


async def verify(seed: bool) -> None:
    async with async_session_factory.begin() as session:
        if seed:
            workspace = await session.scalar(
                text(
                    "INSERT INTO review_agent.workspaces "
                    "(public_id,name,status) VALUES (:id,'migration fixture','active') RETURNING id"
                ),
                {"id": FIXTURE_ID},
            )
            quiz = await session.scalar(
                text(
                    "INSERT INTO review_agent.quizzes "
                    "(public_id,workspace_id,idempotency_key,title,config,requested_scope,"
                    "scope,profile,status) "
                    "VALUES (:id,:workspace,'migration-quiz','Preserved Quiz','{}','{}',"
                    "'[]','fixture','ready') RETURNING id"
                ),
                {"id": uuid4(), "workspace": workspace},
            )
            await session.execute(
                text(
                    "INSERT INTO review_agent.quiz_attempts "
                    "(public_id,workspace_id,quiz_id,idempotency_key,status) "
                    "VALUES (:id,:workspace,:quiz,'migration-attempt','in_progress')"
                ),
                {"id": uuid4(), "workspace": workspace, "quiz": quiz},
            )
        else:
            row = (
                await session.execute(
                    text(
                        "SELECT q.title,a.status,a.revision "
                        "FROM review_agent.quiz_attempts a "
                        "JOIN review_agent.quizzes q ON q.id=a.quiz_id "
                        "AND q.workspace_id=a.workspace_id "
                        "JOIN review_agent.workspaces w ON w.id=a.workspace_id "
                        "WHERE w.public_id=:id"
                    ),
                    {"id": FIXTURE_ID},
                )
            ).one()
            assert tuple(row) == ("Preserved Quiz", "in_progress", 0)
            await session.execute(
                text("DELETE FROM review_agent.workspaces WHERE public_id=:id"), {"id": FIXTURE_ID}
            )
    await close_database()
    print(
        "PASS: migration fixture seeded"
        if seed
        else "PASS: prior Quiz and attempt preserved with revision zero"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--isolated", action="store_true")
    args = parser.parse_args()
    if args.isolated:
        if args.seed:
            parser.error("--isolated performs both seed and verification")
        provision()
        database_url = MigrationSettings().sqlalchemy_database_url  # type: ignore[call-arg]
        asyncio.run(assert_empty(database_url))
        config = Config("alembic.ini")
        command.upgrade(config, "0013_conversation_tasks")
        asyncio.run(verify(True))
        command.upgrade(config, "head")
        asyncio.run(verify(False))
        command.check(config)
    else:
        asyncio.run(verify(args.seed))


if __name__ == "__main__":
    main()
