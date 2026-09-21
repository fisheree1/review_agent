"""Add an observable heartbeat for background workers.

Revision ID: 0005_worker_heartbeat
Revises: 0004_document_tenant_integrity
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.database_schema import APPLICATION_SCHEMA

revision: str = "0005_worker_heartbeat"
down_revision: str | None = "0004_document_tenant_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("worker_type", sa.String(length=64), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("worker_id"),
        schema=APPLICATION_SCHEMA,
    )
    op.create_index(
        "ix_worker_heartbeats_type_seen",
        "worker_heartbeats",
        ["worker_type", "last_seen_at"],
        schema=APPLICATION_SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats", schema=APPLICATION_SCHEMA)
