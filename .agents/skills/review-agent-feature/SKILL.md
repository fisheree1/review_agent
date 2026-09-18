---
name: review-agent-feature
description: Implement or refactor a scoped Review Agent product feature across FastAPI, application/domain code, workers, or the web UI while preserving the repository architecture, privacy, accessibility, and focused testing rules. Do not use for database-only migrations or RAG/Quiz algorithm changes.
---

# Review Agent feature

Deliver a complete, maintainable vertical slice without expanding the requested product scope.

## Before editing

Read `../../../docs/product-requirements.md`, `../../../docs/architecture.md`, and `../../../docs/development-guide.md`. Read `../../../docs/ui-design-system.md` only for user-facing work and `../../../docs/database-design.md` only when persistence changes.

Inspect adjacent modules and tests. State the observable acceptance behavior and identify which domain owns it. Reuse an existing pattern when it preserves the documented dependency direction; do not copy a pattern that violates it.

## Implementation

- Keep transport schemas, application use cases, domain rules, and infrastructure adapters separate where their responsibilities differ.
- Keep routes thin and transactions in the application boundary. Do not perform blocking parsing or model work in the API process.
- Make authorization, idempotency, state transitions, limits, error codes, and empty/error UI states explicit.
- Prefer the smallest cohesive change. Do not add a generic framework or future-facing abstraction without a real second implementation.
- Update OpenAPI, migrations, user copy, and relevant project docs when the contract changes.

## Verification

Choose tests by user risk: one successful outcome, the important denied/invalid outcome, and a regression boundary. Run the nearest tests first; use `review-agent-pr-check` when the change is ready for handoff. Report what was verified and any deliberately deferred scope.
