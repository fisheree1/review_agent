"""Authenticated personal review HTTP contracts."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth import Principal
from app.core.database import async_session_factory
from app.core.errors import ApplicationError
from app.learning.api import Auth, QuizQuestionResponse, RequestKey
from app.learning.review import ReviewRating, ReviewStore
from app.learning.review_store import SqlReviewStore

router = APIRouter(prefix="/api/v1", tags=["review"])


def get_review_store() -> ReviewStore:
    return SqlReviewStore(async_session_factory)


Store = Annotated[ReviewStore, Depends(get_review_store)]


def review_user(principal: Principal) -> UUID:
    if principal.user_public_id is None:
        raise ApplicationError(
            code="USER_REQUIRED", message="请登录个人账号使用长期复习", status_code=403
        )
    return principal.user_public_id


class ReviewItem(BaseModel):
    id: UUID
    due_at: datetime
    revision: int
    review_count: int
    question: QuizQuestionResponse


class UpcomingItem(BaseModel):
    id: UUID
    due_at: datetime
    topic: str
    interval_days: int


class ReviewQueue(BaseModel):
    due_count: int
    upcoming_count: int
    items: list[ReviewItem]
    upcoming: list[UpcomingItem]


class EnrollmentResponse(BaseModel):
    added: int
    existing: int


class RatingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: ReviewRating
    expected_revision: int = Field(ge=0, strict=True)


class RatingResponse(BaseModel):
    id: UUID
    due_at: datetime
    interval_days: int
    revision: int


@router.get("/learning/review", response_model=ReviewQueue)
async def review_queue(principal: Auth, store: Store) -> ReviewQueue:
    return ReviewQueue.model_validate(
        await store.queue(principal.workspace_public_id, review_user(principal))
    )


@router.post(
    "/quizzes/{quiz_id}/attempts/{attempt_id}/review-cards", response_model=EnrollmentResponse
)
async def enroll_review(
    quiz_id: UUID, attempt_id: UUID, principal: Auth, store: Store
) -> EnrollmentResponse:
    return EnrollmentResponse.model_validate(
        await store.enroll(
            principal.workspace_public_id,
            review_user(principal),
            quiz_id,
            attempt_id,
        )
    )


@router.get("/learning/review/{card_id}/answer", response_model=QuizQuestionResponse)
async def reveal_answer(card_id: UUID, principal: Auth, store: Store) -> QuizQuestionResponse:
    return QuizQuestionResponse.model_validate(
        await store.answer(
            principal.workspace_public_id,
            review_user(principal),
            card_id,
        )
    )


@router.post("/learning/review/{card_id}:rate", response_model=RatingResponse)
async def rate_review(
    card_id: UUID, body: RatingRequest, key: RequestKey, principal: Auth, store: Store
) -> RatingResponse:
    return RatingResponse.model_validate(
        await store.rate(
            principal.workspace_public_id,
            review_user(principal),
            card_id,
            body.rating,
            body.expected_revision,
            key,
        )
    )
