"""PostgreSQL adapter for learning organization; all accesses carry ownership."""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import User, WorkspaceMember
from app.core.errors import ApplicationError
from app.documents.infrastructure.models import WorkspaceModel
from app.learning.models import Conversation, ConversationGroup, StudyCheckin
from app.learning.scope import missing, workspace_id
from app.learning.store import conversation_view


class SqlLearningOrganizationStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def _group(
        self, session: AsyncSession, workspace: int, public_id: UUID
    ) -> ConversationGroup:
        group = await session.scalar(
            select(ConversationGroup)
            .where(
                ConversationGroup.workspace_id == workspace,
                ConversationGroup.public_id == public_id,
            )
            .with_for_update()
        )
        if group is None:
            raise missing()
        return group

    async def list_groups(self, workspace: UUID) -> list[dict[str, Any]]:
        async with self.sessions.begin() as session:
            owner = await workspace_id(session, workspace)
            groups = (
                await session.scalars(
                    select(ConversationGroup)
                    .where(ConversationGroup.workspace_id == owner)
                    .order_by(ConversationGroup.created_at, ConversationGroup.id)
                )
            ).all()
            return [{"id": str(group.public_id), "name": group.name} for group in groups]

    async def create_group(self, workspace: UUID, name: str) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            owner = await workspace_id(session, workspace)
            await session.scalar(
                select(WorkspaceModel.id).where(WorkspaceModel.id == owner).with_for_update()
            )
            count = await session.scalar(
                select(func.count())
                .select_from(ConversationGroup)
                .where(ConversationGroup.workspace_id == owner)
            )
            if (count or 0) >= 50:
                raise ApplicationError(
                    code="GROUP_LIMIT", message="最多创建 50 个分组", status_code=422
                )
            group = ConversationGroup(workspace_id=owner, name=name)
            session.add(group)
            await session.flush()
            return {"id": str(group.public_id), "name": group.name}

    async def rename_group(self, workspace: UUID, group: UUID, name: str) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            owner = await workspace_id(session, workspace)
            row = await self._group(session, owner, group)
            row.name = name
            return {"id": str(row.public_id), "name": row.name}

    async def delete_group(self, workspace: UUID, group: UUID) -> None:
        async with self.sessions.begin() as session:
            owner = await workspace_id(session, workspace)
            row = await self._group(session, owner, group)
            await session.execute(
                update(Conversation)
                .where(Conversation.workspace_id == owner, Conversation.group_id == group)
                .values(group_id=None)
            )
            await session.delete(row)

    async def update_conversation(
        self,
        workspace: UUID,
        conversation: UUID,
        title: str | None,
        group: UUID | None,
        change_group: bool,
    ) -> dict[str, Any]:
        async with self.sessions.begin() as session:
            owner = await workspace_id(session, workspace)
            # Match group deletion's lock order: group first, conversation second.
            if change_group and group is not None:
                await self._group(session, owner, group)
            row = await session.scalar(
                select(Conversation)
                .where(Conversation.workspace_id == owner, Conversation.public_id == conversation)
                .with_for_update()
            )
            if row is None:
                raise missing()
            if title is not None:
                row.title = title
            if change_group:
                row.group_id = group
            await session.flush()
            await session.refresh(row, ["updated_at"])
            return conversation_view(row)

    async def delete_conversation(self, workspace: UUID, conversation: UUID) -> None:
        async with self.sessions.begin() as session:
            owner = await workspace_id(session, workspace)
            # Cascades remove messages, feedback, active Agent runs and their call checkpoints.
            deleted = await session.scalar(
                delete(Conversation)
                .where(Conversation.workspace_id == owner, Conversation.public_id == conversation)
                .returning(Conversation.public_id)
            )
            if deleted is None:
                raise missing()

    async def _member(self, session: AsyncSession, workspace: UUID, user: UUID) -> tuple[int, int]:
        row = (
            await session.execute(
                select(WorkspaceMember.workspace_id, User.id)
                .join(User, User.id == WorkspaceMember.user_id)
                .join(WorkspaceModel, WorkspaceModel.id == WorkspaceMember.workspace_id)
                .where(
                    WorkspaceModel.public_id == workspace,
                    WorkspaceModel.status == "active",
                    User.public_id == user,
                    User.status == "active",
                )
            )
        ).one_or_none()
        if row is None:
            raise missing()
        return row[0], row[1]

    async def checkin_dates(self, workspace: UUID, user: UUID) -> list[date]:
        async with self.sessions.begin() as session:
            owner, member = await self._member(session, workspace, user)
            return list(
                await session.scalars(
                    select(StudyCheckin.local_date)
                    .where(StudyCheckin.workspace_id == owner, StudyCheckin.user_id == member)
                    .order_by(StudyCheckin.local_date)
                )
            )

    async def checkin(self, workspace: UUID, user: UUID, day: date, timezone: str) -> None:
        async with self.sessions.begin() as session:
            owner, member = await self._member(session, workspace, user)
            await session.execute(
                insert(StudyCheckin)
                .values(workspace_id=owner, user_id=member, local_date=day, timezone=timezone)
                .on_conflict_do_nothing(constraint="uq_study_checkins_day")
            )
