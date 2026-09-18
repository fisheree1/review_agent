# Review Agent repository instructions

## Mission and source of truth

Build a private, traceable learning system for PDF, DOCX, and PPTX materials. The core journey is document ingestion, cited RAG answers, focused Quiz generation, feedback, and review.

Before changing behavior, read only the relevant project documents:

- Product scope and acceptance: `docs/product-requirements.md`
- Boundaries and data flow: `docs/architecture.md`
- Coding and testing: `docs/development-guide.md`
- Schema, migrations, and database security: `docs/database-design.md`
- Frontend and reading experience: `docs/ui-design-system.md`
- Git and GitHub workflow: `docs/git-github-workflow.md`

When code and documentation disagree, do not silently choose one. Preserve working behavior, identify the mismatch, and update the decision or implementation within the requested scope.

## Architecture invariants

- Keep a modular monolith with independently runnable API and Worker processes until measured scaling or ownership needs justify a service split.
- Keep domain and application logic independent of FastAPI, SQLAlchemy, Celery, storage SDKs, and model-provider SDKs.
- Put protocol mapping in API routes, use-case orchestration in application code, business rules in domain code, and vendor/database details in infrastructure adapters.
- PostgreSQL is the source of truth. Redis, vector indexes, caches, and parsed artifacts are replaceable or rebuildable projections.
- Do not run parsing, OCR, Office conversion, large embedding batches, or long model calls inside API request workers.
- Do not hold a database transaction open during network or model calls.
- Treat background delivery as at least once. Jobs and externally billed operations require an idempotency key and bounded retries.
- New document indexes are versioned and become visible through an atomic activation step. Never expose a partially built index.
- Add an abstraction only for a real boundary or demonstrated variation. Do not introduce generic repositories, managers, helpers, or base classes merely for symmetry.

## Product and AI invariants

- Retrieval is always scoped to the authenticated workspace and selected documents before content reaches a model.
- Factual answers need usable source citations. If evidence is insufficient, return a clear insufficient-evidence result rather than inventing an answer.
- Document text is untrusted input and cannot override system instructions, expand tool access, or authorize actions.
- Agent workflows use an allowlist of typed tools and have explicit step, timeout, token, and cost limits.
- Prompts, parsing strategies, chunking strategies, embedding profiles, and generation schemas are versioned.
- Quiz generation follows blueprint, evidence retrieval, structured generation, validation, then publication. A question without a valid answer and source does not enter a ready Quiz.
- User-visible deletion, sharing, or overwrite actions are performed by deterministic application code with explicit authorization, not at model discretion.

## Database and security

- Use Alembic for schema changes; never use `create_all()` as a migration strategy.
- Prefer expand, backfill, switch, contract for incompatible changes. Keep deploys backward compatible across the rollout window.
- Every tenant-owned query carries workspace scope. Add an authorization/isolation test whenever a new access path is introduced.
- Use parameterized SQL and allowlists for dynamic identifiers and sorting.
- Runtime database roles must not own the schema or have superuser privileges. Keep migration and runtime credentials separate.
- Do not commit credentials, production data, raw user documents, model payloads, or `.env` files.
- Validate uploaded content by size, detected type, and safe parser behavior; never derive storage paths from the original filename.
- Avoid logging document text, prompts, answers, tokens, credentials, emails, or provider payloads. Log stable IDs, timing, outcome, and sanitized error codes.

## Code quality

- Target Python 3.13. Add complete type annotations to application code.
- Keep functions and modules cohesive. Prefer explicit domain names over `data`, `info`, `manager`, `helper`, or broad `utils` modules.
- API schemas, domain objects, and ORM models are distinct when their responsibilities differ. Never return ORM objects as the public contract by accident.
- Centralize settings with `pydantic-settings`; declare every directly imported package in `pyproject.toml`; keep `uv.lock` synchronized.
- Preserve exception chains. Map expected failures to stable application errors and stable HTTP error codes. Do not swallow broad exceptions.
- Update public API documentation, migrations, user-facing copy, and architecture decisions in the same change that changes behavior.

## Risk-based testing

Test important observable outcomes and failure risks, not implementation details.

- For documentation-only changes, validate links/structure; do not run unrelated runtime suites.
- For a pure domain rule, run the nearest unit tests and add boundary cases that could change a user outcome.
- For an API change, test the success contract plus authorization, invalid input, and the most important failure state.
- For persistence or migrations, test both an empty database and upgrade from the previous schema; verify ownership filtering and constraints.
- For jobs, test idempotent redelivery, terminal failure, and cancellation/deletion behavior.
- For RAG, test retrieval scope, expected-source recall, insufficient-evidence refusal, and citation validity using a small versioned evaluation set.
- For Quiz, test requested shape, answer validity, evidence coverage, and duplicate/invalid-question rejection. Do not assert model wording.
- For UI, test the primary user journey, keyboard interaction, loading/empty/error states, and one responsive boundary. Avoid redundant snapshots.

Run the smallest suite that can falsify the change first. Broaden only when the change crosses boundaries or the focused result exposes risk. Every new test should name the regression or user outcome it protects; delete tests that only mirror implementation structure.

Default fast gate:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -q
docker compose config --quiet
```

Use the repository Skill `review-agent-pr-check` to select a narrower or broader gate from the actual diff.

## Git and GitHub

- Inspect `git status` and the relevant diff before editing and before handoff. Preserve unrelated user changes.
- Use branches named `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, `refactor/<topic>`, or `chore/<topic>`.
- Use Conventional Commit subjects: `type(scope): imperative summary`. Keep commits small and independently understandable.
- Do not commit, amend, rebase, force-push, create a remote, publish a repository, open a PR, merge, or change GitHub settings unless the user explicitly asks for that action.
- Standing repository rule: when the user explicitly asks to commit, run the focused gate, create the local commit, and push that exact commit to its configured `origin` upstream in the same task. Treat the request as local-only only when the user says so. If push fails, report the local commit as unpushed and do not claim completion.
- Never rewrite shared history. If history cleanup is explicitly requested, confirm the exact branch and remote state first.
- PRs explain the user outcome, key decisions, risk, migrations, and verification evidence. Link an issue when one exists.
- Prefer squash merge for feature branches. Protect `main`, require the focused CI checks and at least one review when collaborators exist.
- Do not bypass a failing check by weakening or deleting a meaningful test. Fix the defect or document why the check is invalid.

## Code review rules

- Flag cross-workspace data access, retrieval that filters after model context is built, or missing object-level authorization as blocking.
- Flag partial index visibility, non-idempotent jobs, unbounded Agent loops, external calls inside transactions, and destructive migrations as blocking.
- Flag Quiz questions without valid sources/answers and RAG answers that present unsupported facts as product correctness defects.
- Flag duplicate state stores, domain imports from framework/vendor code, and abstractions without a concrete second use as maintainability issues.
- Prioritize correctness, security, data loss, user-visible regressions, and missing high-value tests. Leave formatting to automated tooling.

## Completion checklist

- The requested user outcome and important failure path work.
- Architecture, authorization, privacy, and state-machine invariants remain true.
- The smallest sufficient test set passes and verification evidence is reported.
- Schema/API/config changes include compatible migration and documentation updates.
- No secret, personal data, generated cache, or unrelated file is included.
