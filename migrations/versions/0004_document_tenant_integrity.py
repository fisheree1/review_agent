"""Enforce document version and page tenant ownership.

Revision ID: 0004_document_tenant_integrity
Revises: 0003_document_list_index
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.database_schema import APPLICATION_SCHEMA

revision: str = "0004_document_tenant_integrity"
down_revision: str | None = "0003_document_list_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("workspace_id", sa.BigInteger(), nullable=True),
        schema=APPLICATION_SCHEMA,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {APPLICATION_SCHEMA}.document_versions AS version
            SET workspace_id = document.workspace_id
            FROM {APPLICATION_SCHEMA}.documents AS document
            WHERE document.id = version.document_id
              AND version.workspace_id IS NULL
            """
        )
    )
    op.alter_column(
        "document_versions",
        "workspace_id",
        nullable=False,
        schema=APPLICATION_SCHEMA,
    )
    op.create_foreign_key(
        "fk_document_versions_workspace_id",
        "document_versions",
        "workspaces",
        ["workspace_id"],
        ["id"],
        source_schema=APPLICATION_SCHEMA,
        referent_schema=APPLICATION_SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_document_versions_id_document",
        "document_versions",
        ["id", "document_id"],
        schema=APPLICATION_SCHEMA,
    )
    op.create_unique_constraint(
        "uq_document_versions_id_workspace",
        "document_versions",
        ["id", "workspace_id"],
        schema=APPLICATION_SCHEMA,
    )

    op.drop_constraint(
        "document_pages_document_version_id_fkey",
        "document_pages",
        schema=APPLICATION_SCHEMA,
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_document_pages_version_workspace",
        "document_pages",
        "document_versions",
        ["document_version_id", "workspace_id"],
        ["id", "workspace_id"],
        source_schema=APPLICATION_SCHEMA,
        referent_schema=APPLICATION_SCHEMA,
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "fk_documents_active_version_id",
        "documents",
        schema=APPLICATION_SCHEMA,
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_documents_active_version_owner",
        "documents",
        "document_versions",
        ["active_version_id", "id"],
        ["id", "document_id"],
        source_schema=APPLICATION_SCHEMA,
        referent_schema=APPLICATION_SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_documents_active_version_owner",
        "documents",
        schema=APPLICATION_SCHEMA,
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_documents_active_version_id",
        "documents",
        "document_versions",
        ["active_version_id"],
        ["id"],
        source_schema=APPLICATION_SCHEMA,
        referent_schema=APPLICATION_SCHEMA,
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "fk_document_pages_version_workspace",
        "document_pages",
        schema=APPLICATION_SCHEMA,
        type_="foreignkey",
    )
    op.create_foreign_key(
        "document_pages_document_version_id_fkey",
        "document_pages",
        "document_versions",
        ["document_version_id"],
        ["id"],
        source_schema=APPLICATION_SCHEMA,
        referent_schema=APPLICATION_SCHEMA,
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_document_versions_id_workspace",
        "document_versions",
        schema=APPLICATION_SCHEMA,
        type_="unique",
    )
    op.drop_constraint(
        "uq_document_versions_id_document",
        "document_versions",
        schema=APPLICATION_SCHEMA,
        type_="unique",
    )
    op.drop_constraint(
        "fk_document_versions_workspace_id",
        "document_versions",
        schema=APPLICATION_SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("document_versions", "workspace_id", schema=APPLICATION_SCHEMA)
