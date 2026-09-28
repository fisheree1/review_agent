"""Exercise migration, ownership and deletion in a disposable database only."""

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.auth.models import User, WorkspaceMember
from app.core.config import MigrationSettings
from app.core.database import async_session_factory, close_database
from app.core.errors import ApplicationError
from app.documents.infrastructure.models import WorkspaceModel
from app.learning.models import Conversation, ConversationGroup, ConversationMessage, StudyCheckin
from app.learning.organization_store import SqlLearningOrganizationStore
from app.learning.run_models import AgentRun, AgentStageExecution
from app.learning.store import SqlLearningStore
from scripts.provision_database_roles import main as provision
from scripts.verify_quiz_agent_migration import assert_empty


async def seed_prior() -> None:
    async with async_session_factory.begin() as session:
        workspace = WorkspaceModel(name="preserved", status="active")
        session.add(workspace)
        await session.flush()
        await session.execute(
            text(
                "INSERT INTO review_agent.conversations "
                "(public_id,workspace_id,title,scope) "
                "VALUES (:id,:owner,'Preserved conversation','[]')"
            ),
            {"id": uuid4(), "owner": workspace.id},
        )
    await close_database()


async def verify(previous: bool) -> None:
    organization = SqlLearningOrganizationStore(async_session_factory)
    learning = SqlLearningStore(async_session_factory, "fixture")
    async with async_session_factory.begin() as session:
        if previous:
            preserved = await session.scalar(
                select(Conversation).where(Conversation.title == "Preserved conversation")
            )
            assert preserved is not None and preserved.group_id is None and preserved.scope == []
        first, second = (
            WorkspaceModel(name="first", status="active"),
            WorkspaceModel(name="second", status="active"),
        )
        user, other = (
            User(email_normalized=f"{uuid4()}@example.test"),
            User(email_normalized=f"{uuid4()}@example.test"),
        )
        session.add_all([first, second, user, other])
        await session.flush()
        session.add_all(
            [
                WorkspaceMember(user_id=user.id, workspace_id=first.id),
                WorkspaceMember(user_id=other.id, workspace_id=first.id),
                WorkspaceMember(user_id=other.id, workspace_id=second.id),
            ]
        )
        owner, foreign, actor, other_actor = (
            first.public_id,
            second.public_id,
            user.public_id,
            other.public_id,
        )
        owner_id = first.id
    created = await learning.create_conversation(owner, "新对话", [], [], "repeat-create")
    assert created == await learning.create_conversation(owner, "新对话", [], [], "repeat-create")
    try:
        await learning.create_conversation(owner, "不同标题", [], [], "repeat-create")
    except ApplicationError as exc:
        assert exc.code == "IDEMPOTENCY_CONFLICT" and exc.status_code == 409
    else:
        raise AssertionError("Reused key accepted different conversation data")
    from uuid import UUID

    conversation = UUID(created["id"])
    try:
        await learning.ask(owner, conversation, "empty-question", "不应发送模型")
    except ApplicationError as exc:
        assert exc.code == "SCOPE_NOT_READY"
    else:
        raise AssertionError("Empty conversation started a task")
    group = await organization.create_group(owner, "课程")
    group_id = UUID(group["id"])
    await organization.update_conversation(owner, conversation, "命名", group_id, True)
    assert (await learning.get_conversation(owner, conversation))["group_id"] == str(group_id)
    for action in [
        organization.update_conversation(foreign, conversation, "越权", None, False),
        organization.delete_conversation(foreign, conversation),
        organization.rename_group(foreign, group_id, "越权"),
        organization.delete_group(foreign, group_id),
        organization.checkin_dates(foreign, actor),
    ]:
        try:
            await action
        except ApplicationError as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("Ownership filter failed")
    foreign_group = await organization.create_group(foreign, "其他空间")
    try:
        await organization.update_conversation(
            owner, conversation, None, UUID(foreign_group["id"]), True
        )
    except ApplicationError as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("Cross-workspace grouping allowed")
    try:
        async with async_session_factory.begin() as session:
            await session.execute(
                text(
                    "UPDATE review_agent.conversations "
                    "SET group_id=:group WHERE public_id=:conversation"
                ),
                {"group": UUID(foreign_group["id"]), "conversation": conversation},
            )
    except IntegrityError:
        pass
    else:
        raise AssertionError("Composite ownership constraint failed")
    await organization.delete_group(owner, group_id)
    assert (await learning.get_conversation(owner, conversation))["group_id"] is None
    today = date(2026, 9, 28)
    await asyncio.gather(
        *(organization.checkin(owner, actor, today, "Asia/Shanghai") for _ in range(5))
    )
    assert await organization.checkin_dates(owner, actor) == [today]
    assert await organization.checkin_dates(owner, other_actor) == []
    assert await organization.checkin_dates(foreign, other_actor) == []
    async with async_session_factory.begin() as session:
        row = await session.scalar(
            select(Conversation).where(Conversation.public_id == conversation)
        )
        assert row is not None
        message = ConversationMessage(
            workspace_id=owner_id,
            conversation_id=row.id,
            idempotency_key="active",
            question="fixture",
            scope=[],
            profile="fixture",
            status="queued",
        )
        session.add(message)
        await session.flush()
        run = AgentRun(
            workspace_id=owner_id,
            conversation_id=row.id,
            message_id=message.id,
            graph_version="fixture",
            profile="fixture",
            scope=[],
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(run)
        await session.flush()
        session.add(
            AgentStageExecution(
                workspace_id=owner_id,
                run_id=run.id,
                stage="plan",
                ordinal=0,
                kind="fixture",
                status="returned",
            )
        )
        run_id, message_id = run.id, message.id
    await organization.delete_conversation(owner, conversation)
    async with async_session_factory.begin() as session:
        assert await session.get(ConversationMessage, message_id) is None
        assert await session.get(AgentRun, run_id) is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentStageExecution)
                .where(AgentStageExecution.run_id == run_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(StudyCheckin)
                .where(StudyCheckin.workspace_id == owner_id)
            )
            == 1
        )
        await session.execute(
            text("DELETE FROM review_agent.workspaces WHERE public_id IN (:first,:second)"),
            {"first": owner, "second": foreign},
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ConversationGroup)
                .where(ConversationGroup.workspace_id == owner_id)
            )
            == 0
        )
    await close_database()
    print(
        "PASS: preserved history, scoped CRUD, composite constraints, member isolation, "
        "concurrent check-in, empty-scope refusal and run deletion"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", action="store_true")
    args = parser.parse_args()
    provision()
    asyncio.run(assert_empty(MigrationSettings().sqlalchemy_database_url))  # type: ignore[call-arg]
    config = Config("alembic.ini")
    if args.previous:
        command.upgrade(config, "0014_agent_workflows")
        asyncio.run(seed_prior())
    command.upgrade(config, "head")
    asyncio.run(verify(args.previous))
    command.check(config)


if __name__ == "__main__":
    main()
