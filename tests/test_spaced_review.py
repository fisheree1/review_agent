import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.auth import Principal, get_current_principal
from app.core.errors import ApplicationError
from app.core.observability import record_request
from app.learning.review import REVIEW_INTERVALS, ReviewStore, schedule_review
from app.learning.review_api import get_review_store, router
from app.main import application_error_handler


def test_remembering_extends_intervals_to_cap_and_forgetting_restarts():
    now = datetime(2026, 9, 30, tzinfo=UTC)
    stage, interval, lapses = 0, 0, 0
    for expected in (*REVIEW_INTERVALS, 120):
        result = schedule_review(
            "good", stage=stage, interval_days=interval, lapses=lapses, now=now
        )
        assert result.interval_days == expected
        assert result.due_at == now + timedelta(days=expected)
        stage, interval = result.stage, result.interval_days
    hard = schedule_review("hard", stage=stage, interval_days=interval, lapses=0, now=now)
    assert hard.stage == stage and hard.interval_days == 60
    again = schedule_review("again", stage=stage, interval_days=interval, lapses=0, now=now)
    assert (again.stage, again.interval_days, again.lapses) == (0, 1, 1)
    new_hard = schedule_review("hard", stage=0, interval_days=0, lapses=0, now=now)
    assert new_hard.interval_days == 1


def test_review_api_requires_personal_auth_and_passes_scope_and_validated_rating():
    async def scenario():
        workspace, user, card, quiz, attempt = (uuid4() for _ in range(5))
        store = AsyncMock(spec=ReviewStore)
        store.queue.return_value = {
            "due_count": 0,
            "upcoming_count": 0,
            "items": [],
            "upcoming": [],
        }
        store.enroll.return_value = {"added": 1, "existing": 0}
        store.rate.return_value = {
            "id": card,
            "due_at": datetime.now(UTC),
            "interval_days": 1,
            "revision": 1,
        }
        app = FastAPI()
        app.include_router(router)
        app.add_exception_handler(ApplicationError, application_error_handler)
        app.middleware("http")(record_request)
        app.dependency_overrides[get_review_store] = lambda: store
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/learning/review")).status_code == 401
            app.dependency_overrides[get_current_principal] = lambda: Principal(
                workspace_public_id=workspace
            )
            assert (await client.get("/api/v1/learning/review")).status_code == 403
            store.queue.assert_not_awaited()
            app.dependency_overrides[get_current_principal] = lambda: Principal(
                workspace_public_id=workspace, user_public_id=user
            )
            assert (await client.get("/api/v1/learning/review")).status_code == 200
            store.queue.assert_awaited_once_with(workspace, user)
            path = f"/api/v1/learning/review/{card}:rate"
            for body in [
                {"rating": "easy", "expected_revision": 0},
                {"rating": "good", "expected_revision": -1},
                {"rating": "good", "expected_revision": True},
            ]:
                assert (
                    await client.post(path, json=body, headers={"Idempotency-Key": "rating"})
                ).status_code == 422
            body = {"rating": "good", "expected_revision": 0}
            assert (await client.post(path, json=body)).status_code == 422
            assert (
                await client.post(path, json=body, headers={"Idempotency-Key": "rating"})
            ).status_code == 200
            store.rate.assert_awaited_once_with(workspace, user, card, "good", 0, "rating")
            assert (
                await client.post(f"/api/v1/quizzes/{quiz}/attempts/{attempt}/review-cards")
            ).status_code == 200
            store.enroll.assert_awaited_once_with(workspace, user, quiz, attempt)
            store.answer.side_effect = ApplicationError(
                code="NOT_FOUND", message="不存在", status_code=404
            )
            assert (await client.get(f"/api/v1/learning/review/{card}/answer")).status_code == 404

    asyncio.run(scenario())
