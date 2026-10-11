"""One transition table drives both legacy task runs and composable study plans."""

from __future__ import annotations

import pytest

from app.learning.run_flow import (
    DONE,
    PDF,
    QUIZ,
    REVIEW,
    SUMMARY,
    WAIT,
    WEAK,
    RunFlow,
    Transition,
    run_flow,
    stage_reservation,
)


def composable(*steps: str) -> RunFlow:
    return run_flow({"steps": list(steps)}, composable=True)


def test_full_study_plan_walks_summary_pdf_quiz_wait_review_and_practice() -> None:
    flow = composable("summary", "pdf", "quiz", "review", "practice")
    assert flow.after_plan() == Transition(SUMMARY, "queued")
    assert flow.after_summary(insufficient=False) == Transition(PDF, "queued")
    assert flow.after_pdf() == Transition(QUIZ, "queued")
    assert flow.after_quiz(QUIZ) == Transition(WAIT, "waiting_input")
    assert flow.after_review(has_review=True, weak_topics=True) == Transition(WEAK, "queued")
    assert flow.after_quiz(WEAK) == Transition(DONE, "completed")


def test_insufficient_summary_stops_the_plan_instead_of_quizzing_without_evidence() -> None:
    flow = composable("summary", "quiz")
    assert flow.after_summary(insufficient=True) == Transition(DONE, "completed")
    assert flow.after_summary(insufficient=True, next_batch=2) == Transition(DONE, "completed")


def test_overview_continues_batch_by_batch_before_the_next_step() -> None:
    flow = composable("summary", "pdf")
    assert flow.after_summary(insufficient=False, next_batch=1) == Transition(
        "overview_1", "queued"
    )
    assert flow.after_summary(insufficient=False) == Transition(PDF, "queued")


def test_quiz_without_review_finishes_and_weak_practice_needs_actual_weak_topics() -> None:
    assert composable("quiz").after_quiz(QUIZ) == Transition(DONE, "completed")
    flow = composable("review", "practice")
    assert flow.after_review(has_review=True, weak_topics=False) == Transition(DONE, "completed")
    assert flow.after_review(has_review=False, weak_topics=True) == Transition(DONE, "completed")
    plain = composable("review")
    assert plain.after_review(has_review=True, weak_topics=True) == Transition(DONE, "completed")


def test_second_clarification_completes_instead_of_waiting_forever() -> None:
    assert RunFlow.after_clarification("plan") == Transition("clarify", "waiting_input")
    assert RunFlow.after_clarification("replan") == Transition("clarify", "completed")


@pytest.mark.parametrize(
    ("plan", "stages"),
    [
        ({"action": "answer"}, (SUMMARY,)),
        ({"action": "create_quiz"}, (QUIZ,)),
        ({"action": "review_mistakes"}, (REVIEW,)),
        ({"action": "practice_weak_topics"}, (REVIEW, WEAK)),
        ({"action": "study"}, (SUMMARY, QUIZ)),
        ({"action": "study", "review_after_submit": True}, (SUMMARY, QUIZ, REVIEW)),
        (
            {"action": "study", "review_after_submit": True, "practice_after_review": True},
            (SUMMARY, QUIZ, REVIEW, WEAK),
        ),
        ({"action": "clarify"}, ()),
    ],
)
def test_legacy_actions_map_onto_the_same_steps(plan: dict, stages: tuple[str, ...]) -> None:
    assert run_flow(plan, composable=False).stages == stages


def test_legacy_study_waits_only_when_review_was_requested() -> None:
    assert run_flow({"action": "study"}, composable=False).after_quiz(QUIZ) == Transition(
        DONE, "completed"
    )
    waiting = run_flow({"action": "study", "review_after_submit": True}, composable=False)
    assert waiting.after_quiz(QUIZ) == Transition(WAIT, "waiting_input")


@pytest.mark.parametrize(
    ("flow", "stage", "owns"),
    [
        (composable("quiz"), QUIZ, True),
        (composable("summary", "quiz"), QUIZ, False),
        (composable("review", "practice"), REVIEW, True),
        (composable("review", "practice"), WEAK, False),
        (composable("quiz", "review", "practice"), WEAK, True),
        (run_flow({"action": "create_quiz"}, composable=False), QUIZ, True),
        (run_flow({"action": "study"}, composable=False), QUIZ, False),
        (run_flow({"action": "practice_weak_topics"}, composable=False), REVIEW, True),
        # Legacy weak practice replaced the review result with the practice Quiz.
        (run_flow({"action": "practice_weak_topics"}, composable=False), WEAK, True),
    ],
)
def test_only_the_primary_output_becomes_the_message_result(
    flow: RunFlow, stage: str, owns: bool
) -> None:
    assert flow.owns_message(stage) is owns


def test_local_stages_reserve_less_time_than_model_stages() -> None:
    assert stage_reservation(PDF) < stage_reservation(SUMMARY)
    assert stage_reservation(REVIEW) < stage_reservation(QUIZ)
    assert stage_reservation("overview_3") == stage_reservation(SUMMARY)
