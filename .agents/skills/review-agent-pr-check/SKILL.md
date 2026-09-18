---
name: review-agent-pr-check
description: Perform a focused pre-PR review of Review Agent changes, selecting the smallest high-value test set from the diff and checking architecture, security, data, Git hygiene, documentation, and release risk. Use before handoff, commit, or pull request preparation.
---

# Review Agent PR check

Produce concise evidence that the change is safe without running redundant suites.

## Inspect

Read `git status`, the complete relevant diff, and changed tests. Preserve unrelated work. Map each changed area to an important user or operational failure; if a test cannot be tied to such a risk, do not add it merely for coverage.

## Select checks

- Docs/config only: validate structure, links, parsing, or configuration syntax.
- Python logic: nearest unit/API tests plus Ruff on touched Python; add mypy when application types changed.
- API/auth: success contract, object-level authorization, invalid input, and stable error response.
- Database: migration from prior and empty schema, constraints, isolation, and important query plan.
- Job: redelivery idempotency, terminal failure, and cancellation.
- RAG/Quiz: run the small versioned quality set defined by `review-agent-rag-quiz`; test semantic outcomes, never exact generated wording.
- Docker/runtime: Compose validation and one readiness smoke test when image, dependency, environment, or startup code changed.
- UI: focused component behavior and one critical Playwright journey when the user path changed.

Run checks in increasing cost order and stop expanding when the changed boundaries and named risks have evidence. A cross-cutting change can require the full fast gate from `AGENTS.md`; a narrow change should not.

## Review and handoff

Check for secrets, generated artifacts, unrelated files, unsafe migrations, missing docs, cross-workspace access, unbounded Agent behavior, and duplicated state. Report findings first, then test evidence, remaining risk, and a Conventional Commit suggestion. Do not stage, commit, push, create a remote, open a PR, merge, or change GitHub settings unless the user explicitly requested that external/stateful action.
