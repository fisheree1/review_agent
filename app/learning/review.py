"""Personal spaced review policy and persistence boundary."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

ReviewRating = Literal["again", "hard", "good"]
REVIEW_POLICY_VERSION = "spaced-review-v1"
REVIEW_INTERVALS = (1, 3, 7, 14, 30, 60, 120)


@dataclass(frozen=True)
class ReviewSchedule:
    stage: int
    interval_days: int
    due_at: datetime
    lapses: int


def schedule_review(
    rating: ReviewRating, *, stage: int, interval_days: int, lapses: int, now: datetime
) -> ReviewSchedule:
    if rating == "again":
        next_stage, interval, lapses = 0, 1, lapses + 1
    elif rating == "hard":
        next_stage, interval = stage, max(1, interval_days // 2)
    else:
        next_stage = min(stage + 1, len(REVIEW_INTERVALS) - 1)
        interval = REVIEW_INTERVALS[stage]
    return ReviewSchedule(next_stage, interval, now + timedelta(days=interval), lapses)


class ReviewStore(Protocol):
    async def queue(self, workspace: UUID, user: UUID) -> dict[str, Any]: ...
    async def enroll(
        self, workspace: UUID, user: UUID, quiz: UUID, attempt: UUID
    ) -> dict[str, int]: ...
    async def answer(self, workspace: UUID, user: UUID, card: UUID) -> dict[str, Any]: ...
    async def rate(
        self,
        workspace: UUID,
        user: UUID,
        card: UUID,
        rating: ReviewRating,
        revision: int,
        key: str,
    ) -> dict[str, Any]: ...
