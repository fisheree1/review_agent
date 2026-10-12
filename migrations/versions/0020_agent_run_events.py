"""Notify listeners when a learning run's visible state changes.

Every writer (stage publication, claims, cancellation, Quiz submission bulk updates) changes
``agent_runs`` rows, so a row trigger is the one place that cannot miss a transition. The
payload is only the run's public UUID; listeners re-read the run through the authorized API.
Notifications are delivered on commit, so readers never see an uncommitted state.
"""

from collections.abc import Sequence

from alembic import op

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0020_agent_run_events"
down_revision: str | None = "0019_legacy_messages_to_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANNEL = "agent_run_events"


def upgrade() -> None:
    op.execute(f"""
        CREATE FUNCTION {S}.notify_agent_run_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('{CHANNEL}', NEW.public_id::text);
            RETURN NULL;
        END
        $$
    """)
    op.execute(f"""
        CREATE TRIGGER notify_agent_run_insert
        AFTER INSERT ON {S}.agent_runs
        FOR EACH ROW EXECUTE FUNCTION {S}.notify_agent_run_change()
    """)
    op.execute(f"""
        CREATE TRIGGER notify_agent_run_update
        AFTER UPDATE ON {S}.agent_runs
        FOR EACH ROW
        WHEN (
            OLD.status IS DISTINCT FROM NEW.status
            OR OLD.stage IS DISTINCT FROM NEW.stage
            OR OLD.revision IS DISTINCT FROM NEW.revision
            OR OLD.outputs IS DISTINCT FROM NEW.outputs
        )
        EXECUTE FUNCTION {S}.notify_agent_run_change()
    """)


def downgrade() -> None:
    op.execute(f"DROP TRIGGER notify_agent_run_update ON {S}.agent_runs")
    op.execute(f"DROP TRIGGER notify_agent_run_insert ON {S}.agent_runs")
    op.execute(f"DROP FUNCTION {S}.notify_agent_run_change()")
