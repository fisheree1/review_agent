"""Hand queued legacy conversation messages to durable runs.

Messages were processed directly by a message worker when the Agent workflow switch was off.
That worker is removed: every message now has an ``agent_runs`` row. Messages still queued or
processing without a run get a current-version run starting at planning, so they finish
instead of waiting forever. Data only; no schema change. Stop old workers before upgrading.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0019_legacy_messages_to_runs"
down_revision: str | None = "0018_chunk_search_terms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MARKER = "legacy-message"
GRAPH_VERSION = "study-graph-v5"  # Fixed at authoring time; later versions do not rewrite this.
WAIT_DAYS = 7


def upgrade() -> None:
    op.execute(
        sa.text(
            f"""
            WITH pending AS (
                SELECT m.id, m.workspace_id, m.conversation_id, m.profile, m.scope
                FROM {S}.conversation_messages AS m
                WHERE m.status IN ('queued', 'processing')
                  AND NOT EXISTS (
                      SELECT 1 FROM {S}.agent_runs AS r
                      WHERE r.message_id = m.id AND r.workspace_id = m.workspace_id
                  )
                FOR UPDATE OF m
            ), created AS (
                INSERT INTO {S}.agent_runs (
                    public_id, workspace_id, conversation_id, message_id, graph_version,
                    profile, scope, status, stage, usage, expires_at
                )
                SELECT gen_random_uuid(), p.workspace_id, p.conversation_id, p.id,
                       :graph_version, p.profile, p.scope, 'queued', 'plan',
                       jsonb_build_object('migrated_from', :marker),
                       now() + make_interval(days => :wait_days)
                FROM pending AS p
                RETURNING message_id, workspace_id
            )
            UPDATE {S}.conversation_messages AS m
            SET status = 'queued', fence = NULL, lease_until = NULL
            FROM created AS c
            WHERE m.id = c.message_id AND m.workspace_id = c.workspace_id
            """
        ).bindparams(graph_version=GRAPH_VERSION, marker=MARKER, wait_days=WAIT_DAYS)
    )


def downgrade() -> None:
    # Only runs that never started return their messages to the legacy worker.
    op.execute(
        sa.text(
            f"""
            DELETE FROM {S}.agent_runs
            WHERE usage ->> 'migrated_from' = :marker
              AND status = 'queued' AND stage = 'plan' AND revision = 0
            """
        ).bindparams(marker=MARKER)
    )
