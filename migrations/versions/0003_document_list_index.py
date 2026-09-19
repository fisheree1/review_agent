"""Add the document library cursor index.

Revision ID: 0003_document_list_index
Revises: 0002_document_ingestion
Create Date: 2026-09-19
"""

from collections.abc import Sequence

from alembic import op

from app.core.database_schema import APPLICATION_SCHEMA

revision: str = "0003_document_list_index"
down_revision: str | None = "0002_document_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_documents_workspace_created",
        "documents",
        ["workspace_id", "created_at", "id"],
        schema=APPLICATION_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_documents_workspace_created",
        table_name="documents",
        schema=APPLICATION_SCHEMA,
    )
