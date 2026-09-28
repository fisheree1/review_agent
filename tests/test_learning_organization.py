import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.auth import Principal, get_current_principal
from app.core.errors import ApplicationError
from app.core.observability import record_request
from app.learning.organization import (
    LearningOrganizationService,
    calendar_view,
    conversation_label,
    local_today,
)
from app.learning.organization_api import get_organization_service, router
from app.main import application_error_handler


def test_calendar_counts_consecutive_days_across_months_and_preserves_yesterday_streak() -> None:
    days = [date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 1)]
    result = calendar_view(days, date(2026, 9, 1), date(2026, 9, 2), "Asia/Shanghai")
    assert result["dates"] == [date(2026, 9, 1)]
    assert (result["streak"], result["total"], result["checked_today"]) == (3, 3, False)
    assert calendar_view(days, date(2026, 9, 1), date(2026, 9, 3), "UTC")["streak"] == 0


def test_checkin_day_uses_server_time_in_requested_timezone() -> None:
    now = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)
    assert local_today("America/Los_Angeles", now) == date(2026, 9, 27)
    assert local_today("Asia/Shanghai", now) == date(2026, 9, 28)
    with pytest.raises(ApplicationError):
        local_today("../private")
    with pytest.raises(ApplicationError):
        conversation_label("   ")


def test_calendar_rejects_invalid_month_before_writing() -> None:
    store = AsyncMock()
    service = LearningOrganizationService(store)
    with pytest.raises(ApplicationError):
        asyncio.run(service.calendar(uuid4(), uuid4(), "2026-13", "UTC", checkin=True))
    store.checkin.assert_not_called()


def test_organization_api_maps_authenticated_scope_and_rejects_invalid_controls() -> None:
    async def scenario() -> None:
        workspace, user, conversation, group = uuid4(), uuid4(), uuid4(), uuid4()
        service = AsyncMock()
        service.update_conversation.return_value = {
            "id": conversation,
            "title": "复习",
            "scope": [],
            "created_at": datetime.now(UTC),
            "group_id": group,
        }
        service.create_group.return_value = {"id": group, "name": "课程"}
        application = FastAPI()
        application.include_router(router)
        application.add_exception_handler(ApplicationError, application_error_handler)
        application.middleware("http")(record_request)
        application.dependency_overrides[get_current_principal] = lambda: Principal(
            workspace_public_id=workspace, user_public_id=user
        )
        application.dependency_overrides[get_organization_service] = lambda: service
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.patch(
                f"/api/v1/conversations/{conversation}", json={"group_id": str(group)}
            )
            assert response.status_code == 200
            service.update_conversation.assert_awaited_once_with(
                workspace, conversation, None, group, True
            )
            assert (
                await client.patch(f"/api/v1/conversations/{conversation}", json={"title": ""})
            ).status_code == 422
            assert (
                await client.get("/api/v1/learning/checkins?month=not-a-month")
            ).status_code == 422
            assert (
                await client.post("/api/v1/conversation-groups", json={"name": "课程"})
            ).status_code == 201
            assert (await client.delete(f"/api/v1/conversations/{conversation}")).status_code == 204
            service.store.delete_conversation.assert_awaited_once_with(workspace, conversation)
        application.dependency_overrides.clear()
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/conversation-groups")).status_code == 401

    asyncio.run(scenario())
