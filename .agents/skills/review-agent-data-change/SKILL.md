---
name: review-agent-data-change
description: Design, implement, or review Review Agent PostgreSQL, pgvector, SQLAlchemy, repository, transaction, migration, tenancy, backup, or data-lifecycle changes. Use when a task changes persisted data or database behavior.
---

# Review Agent data change

Evolve persisted data without weakening isolation, recoverability, or rollout safety.

## Required context

Read `../../../docs/database-design.md` and the relevant flow in `../../../docs/architecture.md`. Inspect all callers and the current migration head before proposing a schema change.

## Design and implementation

- Identify ownership, lifecycle, source of truth, sensitivity, expected volume, and primary query paths.
- Keep `workspace_id` on tenant-owned access paths and enforce scope in repository queries. Add isolation tests.
- Use Alembic. Prefer expand, backfill, switch, contract; avoid table rewrites and long locks in deploy-time migrations.
- Keep runtime and migration privileges separate. Never use a superuser connection in application configuration.
- Do not hold transactions across storage/model/network calls. Make concurrent updates and retries explicit.
- Use explicit columns for filtered, sorted, joined, or constrained values; reserve JSONB for genuinely variable metadata.
- Add indexes from actual query shapes. For vector changes, record dimensions, distance function, filter selectivity, recall baseline, index size, and rebuild plan.
- Describe deletion propagation across PostgreSQL, object storage, caches, and derived vectors.

## Verification

Verify migration from an empty database and from the previous schema. Test critical constraints, rollback/recovery instructions, tenant isolation, and representative query plans. Do not claim safety from model tests alone or from a migration that only succeeds on an empty database.
