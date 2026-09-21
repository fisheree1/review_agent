"""Bind every document version to its document's workspace.

Revision ID: 0006_document_version_owner
Revises: 0005_worker_heartbeat
Create Date: 2026-09-21
"""

from collections.abc import Sequence

from alembic import op

from app.core.database_schema import APPLICATION_SCHEMA

revision: str = "0006_document_version_owner"
down_revision: str | None = "0005_worker_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_documents_id_workspace",
        "documents",
        ["id", "workspace_id"],
        schema=APPLICATION_SCHEMA,
    )
    op.drop_constraint(
        "document_versions_document_id_fkey",
        "document_versions",
        schema=APPLICATION_SCHEMA,
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_document_versions_document_workspace",
        "document_versions",
        "documents",
        ["document_id", "workspace_id"],
        ["id", "workspace_id"],
        source_schema=APPLICATION_SCHEMA,
        referent_schema=APPLICATION_SCHEMA,
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_document_versions_document_workspace",
        "document_versions",
        schema=APPLICATION_SCHEMA,
        type_="foreignkey",
    )
    op.create_foreign_key(
        "document_versions_document_id_fkey",
        "document_versions",
        "documents",
        ["document_id"],
        ["id"],
        source_schema=APPLICATION_SCHEMA,
        referent_schema=APPLICATION_SCHEMA,
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_documents_id_workspace",
        "documents",
        schema=APPLICATION_SCHEMA,
        type_="unique",
    )
