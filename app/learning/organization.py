"""Conversation organization and personal daily check-ins."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import ApplicationError


class LearningOrganizationStore(Protocol):
    async def list_groups(self, workspace: UUID) -> list[dict[str, Any]]: ...
    async def create_group(self, workspace: UUID, name: str) -> dict[str, Any]: ...
    async def rename_group(self, workspace: UUID, group: UUID, name: str) -> dict[str, Any]: ...
    async def delete_group(self, workspace: UUID, group: UUID) -> None: ...
    async def update_conversation(
        self,
        workspace: UUID,
        conversation: UUID,
        title: str | None,
        group: UUID | None,
        change_group: bool,
    ) -> dict[str, Any]: ...
    async def delete_conversation(self, workspace: UUID, conversation: UUID) -> None: ...
    async def checkin_dates(self, workspace: UUID, user: UUID) -> list[date]: ...
    async def checkin(self, workspace: UUID, user: UUID, day: date, timezone: str) -> None: ...


def conversation_label(value: str, *, group: bool = False) -> str:
    value = value.strip()
    if not 1 <= len(value) <= (80 if group else 160):
        raise ApplicationError(
            code="CONVERSATION_LABEL_INVALID", message="名称不能为空或超过长度限制", status_code=422
        )
    return value


def local_today(timezone: str, now: datetime | None = None) -> date:
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ApplicationError(
            code="TIMEZONE_INVALID", message="请选择有效时区", status_code=422
        ) from exc
    return (now or datetime.now(UTC)).astimezone(zone).date()


def calendar_view(dates: list[date], month: date, today: date, timezone: str) -> dict[str, Any]:
    checked = set(dates)
    end = today if today in checked else today - timedelta(days=1)
    streak = 0
    while end in checked:
        streak += 1
        end -= timedelta(days=1)
    return {
        "month": month.strftime("%Y-%m"),
        "today": today,
        "timezone": timezone,
        "dates": sorted(
            day for day in checked if (day.year, day.month) == (month.year, month.month)
        ),
        "checked_today": today in checked,
        "streak": streak,
        "total": len(checked),
    }


class LearningOrganizationService:
    def __init__(self, store: LearningOrganizationStore) -> None:
        self.store = store

    async def create_group(self, workspace: UUID, name: str) -> dict[str, Any]:
        return await self.store.create_group(workspace, conversation_label(name, group=True))

    async def rename_group(self, workspace: UUID, group: UUID, name: str) -> dict[str, Any]:
        return await self.store.rename_group(workspace, group, conversation_label(name, group=True))

    async def update_conversation(
        self,
        workspace: UUID,
        conversation: UUID,
        title: str | None,
        group: UUID | None,
        change_group: bool,
    ) -> dict[str, Any]:
        return await self.store.update_conversation(
            workspace,
            conversation,
            conversation_label(title) if title is not None else None,
            group,
            change_group,
        )

    async def calendar(
        self,
        workspace: UUID,
        user: UUID,
        month: str | None,
        timezone: str,
        *,
        checkin: bool = False,
    ) -> dict[str, Any]:
        today = local_today(timezone)
        try:
            selected = date.fromisoformat(f"{month}-01") if month else today.replace(day=1)
        except ValueError as exc:
            raise ApplicationError(
                code="MONTH_INVALID", message="月份格式应为 YYYY-MM", status_code=422
            ) from exc
        if checkin:
            await self.store.checkin(workspace, user, today, timezone)
        return calendar_view(
            await self.store.checkin_dates(workspace, user), selected, today, timezone
        )
