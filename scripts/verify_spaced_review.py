"""Verify personal review in a provisioned, disposable database; never production."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.auth.models import User, WorkspaceMember
from app.core.config import MigrationSettings
from app.core.database import async_session_factory as sessions
from app.core.database import close_database
from app.core.errors import ApplicationError
from app.documents.infrastructure.models import DocumentModel, DocumentVersionModel, WorkspaceModel
from app.learning.models import Quiz, QuizAnswer, QuizAttempt, QuizDocument, QuizQuestion
from app.learning.review_models import ReviewCard, ReviewEvent
from app.learning.review_store import SqlReviewStore
from scripts.provision_database_roles import main as provision
from scripts.verify_agent_workflow_migration import verify as prior_fixture
from scripts.verify_learning_flow import seed_document
from scripts.verify_quiz_agent_migration import assert_empty


async def rejected(action: Any, status: int) -> None:
    try:
        await action
    except ApplicationError as exc:
        assert exc.status_code == status, exc.code
    else:
        raise AssertionError("Expected rejected access or stale action")


async def verify() -> None:
    store = SqlReviewStore(sessions)
    async with sessions.begin() as session:
        owner, foreign = WorkspaceModel(name="review fixture"), WorkspaceModel(name="foreign")
        user, other = (
            User(email_normalized=f"{uuid4()}@example.test"),
            User(email_normalized=f"{uuid4()}@example.test"),
        )
        session.add_all([owner, foreign, user, other])
        await session.flush()
        session.add_all(
            [
                WorkspaceMember(user_id=user.id, workspace_id=owner.id),
                WorkspaceMember(user_id=other.id, workspace_id=owner.id),
                WorkspaceMember(user_id=other.id, workspace_id=foreign.id),
            ]
        )
    doc = await seed_document(owner, "review-fixture.pdf")
    async with sessions.begin() as session:
        version = await session.scalar(
            select(DocumentModel.active_version_id).where(
                DocumentModel.workspace_id == owner.id, DocumentModel.public_id == doc
            )
        )
        quiz = Quiz(
            workspace_id=owner.id,
            idempotency_key="review",
            title="Review fixture",
            config={},
            requested_scope={},
            scope=[],
            profile="fixture",
            status="ready",
        )
        session.add(quiz)
        await session.flush()
        session.add(
            QuizDocument(workspace_id=owner.id, quiz_id=quiz.id, document_version_id=version)
        )
        questions = [
            QuizQuestion(
                workspace_id=owner.id,
                quiz_id=quiz.id,
                ordinal=i,
                kind="single",
                difficulty="easy",
                topic=f"Topic {i}",
                stem=f"Recall {i}?",
                options=["A", "B"],
                answer="A",
                explanation="Explanation",
                sources=[],
                schema_version="fixture",
            )
            for i in (1, 2, 3)
        ]
        attempt = QuizAttempt(
            workspace_id=owner.id, quiz_id=quiz.id, idempotency_key="attempt", status="in_progress"
        )
        session.add_all([*questions, attempt])
        await session.flush()
        session.add_all(
            [
                QuizAnswer(
                    workspace_id=owner.id,
                    attempt_id=attempt.id,
                    question_id=questions[0].id,
                    response="B",
                    score=0,
                ),
                QuizAnswer(
                    workspace_id=owner.id,
                    attempt_id=attempt.id,
                    question_id=questions[1].id,
                    response="A",
                    score=0.7,
                ),
            ]
        )
    w, u, q, a = owner.public_id, user.public_id, quiz.public_id, attempt.public_id
    await rejected(store.enroll(w, u, q, a), 409)
    async with sessions.begin() as session:
        await session.execute(
            update(QuizAttempt)
            .where(QuizAttempt.workspace_id == owner.id, QuizAttempt.id == attempt.id)
            .values(status="submitted", submitted_at=datetime.now(UTC))
        )
    results = await asyncio.gather(*(store.enroll(w, u, q, a) for _ in range(3)))
    assert (
        sum(result["added"] for result in results) == 2
    )  # wrong and unanswered; threshold .7 excluded
    queue = await store.queue(w, u)
    assert queue["due_count"] == 2 and queue["upcoming_count"] == 0
    assert all(
        item["question"]["answer"] is None
        and item["question"]["sources"] == []
        and item["question"]["explanation"] is None
        for item in queue["items"]
    )
    card = UUID(queue["items"][0]["id"])
    assert (await store.answer(w, u, card))["answer"] == "A"
    assert (await store.queue(w, other.public_id))["due_count"] == 0
    assert (await store.queue(foreign.public_id, other.public_id))["due_count"] == 0
    for actor_w, actor_u in [
        (w, other.public_id),
        (foreign.public_id, other.public_id),
        (foreign.public_id, u),
    ]:
        await rejected(store.answer(actor_w, actor_u, card), 404)
        await rejected(store.rate(actor_w, actor_u, card, "good", 0, "other"), 404)
    await rejected(store.enroll(foreign.public_id, other.public_id, q, a), 404)
    first, second = await asyncio.gather(
        *(store.rate(w, u, card, "good", 0, "rating") for _ in range(2))
    )
    assert first == second and first["revision"] == 1 and first["interval_days"] == 1
    await rejected(store.rate(w, u, card, "again", 0, "rating"), 409)
    await rejected(store.rate(w, u, card, "good", 0, "new-key"), 409)
    await rejected(store.rate(w, u, card, "good", 1, "too-soon"), 409)
    assert (await store.enroll(w, u, q, a)) == {"added": 0, "existing": 2}
    queue = await store.queue(w, u)
    assert queue["due_count"] == 1 and queue["upcoming_count"] == 1
    async with sessions.begin() as session:
        await session.execute(
            update(ReviewCard)
            .where(
                ReviewCard.workspace_id == owner.id,
                ReviewCard.user_id == user.id,
                ReviewCard.public_id == card,
            )
            .values(due_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    await store.rate(w, u, card, "good", 1, "second-rating")
    assert (
        await store.rate(w, u, card, "good", 0, "rating") == first
    )  # durable replay after later review
    # Two distinct concurrent submissions must not both advance the same revision.
    remaining = UUID((await store.queue(w, u))["items"][0]["id"])
    concurrent = await asyncio.gather(
        store.rate(w, u, remaining, "good", 0, "tab-a"),
        store.rate(w, u, remaining, "again", 0, "tab-b"),
        return_exceptions=True,
    )
    assert sum(isinstance(item, dict) for item in concurrent) == 1
    assert any(
        isinstance(item, ApplicationError) and item.status_code == 409 for item in concurrent
    )
    try:
        async with sessions.begin() as session:
            session.add(
                ReviewCard(workspace_id=foreign.id, user_id=other.id, question_id=questions[0].id)
            )
    except IntegrityError:
        pass
    else:
        raise AssertionError("Cross-workspace question FK accepted")
    # A member's deletion removes only that member's personal state.
    await store.enroll(w, other.public_id, q, a)
    async with sessions.begin() as session:
        await session.execute(
            delete(WorkspaceMember).where(
                WorkspaceMember.workspace_id == owner.id, WorkspaceMember.user_id == other.id
            )
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ReviewCard)
                .where(ReviewCard.workspace_id == owner.id, ReviewCard.user_id == other.id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ReviewCard)
                .where(ReviewCard.workspace_id == owner.id, ReviewCard.user_id == user.id)
            )
            == 2
        )
        # Confirm the due index can service the queue's owner/time/order predicate.
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await session.scalars(
            text(
                "EXPLAIN SELECT id FROM review_agent.review_cards "
                "WHERE workspace_id=:w AND user_id=:u AND due_at <= now() "
                "ORDER BY due_at,id LIMIT 20"
            ),
            {"w": owner.id, "u": user.id},
        )
        assert "ix_review_card_due" in " ".join(plan)
    async with sessions.begin() as session:
        await session.execute(
            update(DocumentModel)
            .where(
                DocumentModel.workspace_id == owner.id,
                DocumentModel.public_id == doc,
            )
            .values(status="deleting")
        )
    assert (await store.queue(w, u))["upcoming_count"] == 0
    await rejected(store.answer(w, u, card), 404)
    await rejected(store.enroll(w, u, q, a), 404)
    # Existing parse-version purge trigger cascades through Quiz -> cards -> events.
    async with sessions.begin() as session:
        await session.execute(
            update(DocumentModel)
            .where(DocumentModel.workspace_id == owner.id, DocumentModel.public_id == doc)
            .values(active_version_id=None)
        )
        await session.execute(
            delete(DocumentVersionModel).where(
                DocumentVersionModel.workspace_id == owner.id, DocumentVersionModel.id == version
            )
        )
        for model in [ReviewCard, ReviewEvent]:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.__table__.c.workspace_id == owner.id)
                )
                == 0
            )
        assert await session.get(Quiz, quiz.id) is None
    assert (await store.queue(w, u))["due_count"] == 0
    await rejected(store.answer(w, u, card), 404)
    await close_database()
    print(
        "PASS: enrollment, hidden answers, personal/workspace isolation, receipt replay, "
        "concurrent revisions, due index, member/source deletion"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated", action="store_true", required=True)
    parser.add_argument("--previous", action="store_true")
    args = parser.parse_args()
    provision()
    asyncio.run(assert_empty(MigrationSettings().sqlalchemy_database_url))  # type: ignore[call-arg]
    config = Config("alembic.ini")
    if args.previous:
        command.upgrade(config, "0016_conversation_memory")
        asyncio.run(prior_fixture(True))
    command.upgrade(config, "head")
    if args.previous:
        asyncio.run(prior_fixture(False))
    asyncio.run(verify())
    command.check(config)


if __name__ == "__main__":
    main()
