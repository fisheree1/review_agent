"""Pure stage transitions for durable learning runs.

Stores call these rules after persisting a stage's output; they never decide transitions
themselves. Composable runs carry an ordered ``steps`` plan. Legacy ``study-graph-v1`` runs
carry one task ``action`` and are normalized onto the same steps, so both follow one table.

Quiz submission moves ``wait`` runs to ``grade`` or ``review`` inside the attempt transaction
(see ``SqlLearningStore``); those stage names are defined here as well.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PLAN, REPLAN, CLARIFY = "plan", "replan", "clarify"
SUMMARY, PDF, QUIZ, WEAK, REVIEW, GRADE = "summary", "pdf", "quiz", "weak", "review", "grade"
WAIT, DONE = "wait", "done"
OVERVIEW_PREFIX = "overview_"

RunStatus = Literal["queued", "waiting_input", "completed"]

# The weak-topic practice step runs as the ``weak`` stage.
_STAGE_FOR_STEP = {"practice": WEAK}

# Legacy task actions expressed as composable steps.
_LEGACY_STEPS: dict[str, tuple[str, ...]] = {
    "answer": (SUMMARY,),
    "create_quiz": (QUIZ,),
    "review_mistakes": (REVIEW,),
    "practice_weak_topics": (REVIEW, "practice"),
    "study": (SUMMARY, QUIZ),
}


@dataclass(frozen=True, slots=True)
class Transition:
    stage: str
    status: RunStatus


@dataclass(frozen=True, slots=True)
class RunFlow:
    steps: tuple[str, ...]
    legacy_action: str | None = None

    @property
    def stages(self) -> tuple[str, ...]:
        return tuple(_STAGE_FOR_STEP.get(step, step) for step in self.steps)

    def _following(self, stage: str) -> Transition:
        stages = self.stages
        position = stages.index(stage)
        if position + 1 < len(stages):
            return Transition(stages[position + 1], "queued")
        return Transition(DONE, "completed")

    def after_plan(self) -> Transition:
        if not self.steps:
            raise ValueError("A plan without steps must clarify instead")
        return Transition(self.stages[0], "queued")

    @staticmethod
    def after_clarification(stage: str) -> Transition:
        # A second clarification terminates instead of an unbounded loop.
        return Transition(CLARIFY, "completed" if stage == REPLAN else "waiting_input")

    def after_summary(self, *, insufficient: bool, next_batch: int | None = None) -> Transition:
        if next_batch is not None and not insufficient:
            return Transition(f"{OVERVIEW_PREFIX}{next_batch}", "queued")
        if insufficient:
            return Transition(DONE, "completed")
        return self._following(SUMMARY)

    def after_pdf(self) -> Transition:
        return self._following(PDF)

    def after_quiz(self, stage: str) -> Transition:
        # A weak-topic Quiz is the final output; never recursively schedule more practice.
        if stage == QUIZ and REVIEW in self.stages:
            return Transition(WAIT, "waiting_input")
        return Transition(DONE, "completed")

    def after_review(self, *, has_review: bool, weak_topics: bool) -> Transition:
        if WEAK in self.stages and has_review and weak_topics:
            return Transition(WEAK, "queued")
        return Transition(DONE, "completed")

    def owns_message(self, stage: str) -> bool:
        """Whether this stage's output becomes the conversation message's task result."""
        if stage in (QUIZ, WEAK):
            # Quiz-first plans (and legacy weak practice) show their Quizzes on the message.
            return self.stages[:1] == (QUIZ,) or self.legacy_action == "practice_weak_topics"
        return self.stages[:1] == (stage,)


def run_flow(plan: dict[str, Any] | None, *, composable: bool) -> RunFlow:
    plan = plan or {}
    if composable:
        return RunFlow(tuple(plan.get("steps", ())))
    action = plan.get("action")
    if not isinstance(action, str) or action not in _LEGACY_STEPS:
        return RunFlow((), action if isinstance(action, str) else None)
    steps = list(_LEGACY_STEPS[action])
    if action == "study" and plan.get("review_after_submit"):
        steps.append(REVIEW)
        if plan.get("practice_after_review"):
            steps.append("practice")
    return RunFlow(tuple(steps), action)


# Seconds reserved against the run's time budget when a stage is claimed. Stages without a
# model call reserve little; the unused part is refunded from the measured duration.
STAGE_TIMEOUT_SECONDS = 110
_LOCAL_STAGE_RESERVATION = 15


def stage_reservation(stage: str) -> int:
    return _LOCAL_STAGE_RESERVATION if stage in (PDF, REVIEW) else STAGE_TIMEOUT_SECONDS
