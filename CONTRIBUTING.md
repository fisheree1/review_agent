# Contributing

## Start with the contract

Read `AGENTS.md` and the documents linked from `docs/README.md`. Open or link an issue for behavior changes large enough to need product discussion.

## Branches and commits

- Branch from an up-to-date `main` using `feat/`, `fix/`, `docs/`, `refactor/`, or `chore/`.
- Use Conventional Commit subjects, for example `feat(documents): add upload status endpoint`.
- Keep commits focused; do not combine formatting, dependency upgrades, and behavior changes without a reason.
- Never commit `.env`, credentials, real user documents, database dumps, or model payloads.
- Push each completed commit to its branch on `origin`; do not leave a supposedly shared change only in the local repository.

## Focused verification

Select tests from the risk created by the diff. Run a narrow check first and expand only across affected boundaries.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -q
docker compose config --quiet
```

Database, Docker, RAG, Quiz, and UI changes have additional focused requirements in `AGENTS.md`. A PR must explain the important outcome protected by each added test.

## Pull requests

- Keep PRs small enough to review as one coherent decision.
- Complete the template with user outcome, risk, verification evidence, and migration/UI notes.
- Resolve CI and review findings without weakening meaningful checks.
- Prefer squash merge. Delete merged branches and keep `main` protected.
