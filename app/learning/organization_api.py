"""HTTP contracts for conversation organization and daily study check-ins."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from app.core.auth import Principal, get_current_principal
from app.core.database import async_session_factory
from app.core.errors import ApplicationError
from app.learning.api import ConversationResponse
from app.learning.organization import LearningOrganizationService
from app.learning.organization_store import SqlLearningOrganizationStore

router = APIRouter(prefix="/api/v1", tags=["learning"])
Auth = Annotated[Principal, Depends(get_current_principal)]


def get_organization_service() -> LearningOrganizationService:
    return LearningOrganizationService(SqlLearningOrganizationStore(async_session_factory))


Service = Annotated[LearningOrganizationService, Depends(get_organization_service)]


class GroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class GroupResponse(GroupRequest):
    id: UUID


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    group_id: UUID | None = None


class CheckinRequest(BaseModel):
    timezone: str = Field(min_length=1, max_length=100)


class CalendarResponse(BaseModel):
    month: str
    today: date
    timezone: str
    dates: list[date]
    checked_today: bool
    streak: int
    total: int


def checkin_user(principal: Principal) -> UUID:
    if principal.user_public_id is None:
        raise ApplicationError(
            code="USER_REQUIRED", message="请使用个人账号登录后打卡", status_code=403
        )
    return principal.user_public_id


@router.get("/conversation-groups", response_model=list[GroupResponse])
async def list_groups(principal: Auth, service: Service) -> list[GroupResponse]:
    return [
        GroupResponse.model_validate(item)
        for item in await service.store.list_groups(principal.workspace_public_id)
    ]


@router.post("/conversation-groups", response_model=GroupResponse, status_code=201)
async def create_group(body: GroupRequest, principal: Auth, service: Service) -> GroupResponse:
    return GroupResponse.model_validate(
        await service.create_group(principal.workspace_public_id, body.name)
    )


@router.patch("/conversation-groups/{group_id}", response_model=GroupResponse)
async def rename_group(
    group_id: UUID, body: GroupRequest, principal: Auth, service: Service
) -> GroupResponse:
    return GroupResponse.model_validate(
        await service.rename_group(principal.workspace_public_id, group_id, body.name)
    )


@router.delete("/conversation-groups/{group_id}", status_code=204)
async def delete_group(group_id: UUID, principal: Auth, service: Service) -> Response:
    await service.store.delete_group(principal.workspace_public_id, group_id)
    return Response(status_code=204)


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: UUID, body: ConversationUpdate, principal: Auth, service: Service
) -> ConversationResponse:
    return ConversationResponse.model_validate(
        await service.update_conversation(
            principal.workspace_public_id,
            conversation_id,
            body.title,
            body.group_id,
            "group_id" in body.model_fields_set,
        )
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: UUID, principal: Auth, service: Service) -> Response:
    await service.store.delete_conversation(principal.workspace_public_id, conversation_id)
    return Response(status_code=204)


@router.get("/learning/checkins", response_model=CalendarResponse)
async def get_calendar(
    principal: Auth,
    service: Service,
    timezone: Annotated[str, Query(min_length=1, max_length=100)] = "UTC",
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
) -> CalendarResponse:
    return CalendarResponse.model_validate(
        await service.calendar(
            principal.workspace_public_id,
            checkin_user(principal),
            month,
            timezone,
        )
    )


@router.post("/learning/checkins", response_model=CalendarResponse)
async def checkin(body: CheckinRequest, principal: Auth, service: Service) -> CalendarResponse:
    return CalendarResponse.model_validate(
        await service.calendar(
            principal.workspace_public_id,
            checkin_user(principal),
            None,
            body.timezone,
            checkin=True,
        )
    )
