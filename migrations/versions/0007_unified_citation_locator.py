"""Add unified citation locators for PDF, DOCX, and PPTX content.

Revision ID: 0007_unified_citation_locator
Revises: 0006_document_version_owner
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.database_schema import APPLICATION_SCHEMA

revision: str = "0007_unified_citation_locator"
down_revision: str | None = "0006_document_version_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_pages",
        sa.Column("locator_kind", sa.String(length=20), nullable=True),
        schema=APPLICATION_SCHEMA,
    )
    op.add_column(
        "document_pages",
        sa.Column("locator_position", sa.Integer(), nullable=True),
        schema=APPLICATION_SCHEMA,
    )
    op.add_column(
        "document_pages",
        sa.Column("locator_title", sa.String(length=300), nullable=True),
        schema=APPLICATION_SCHEMA,
    )
    op.add_column(
        "document_pages",
        sa.Column(
            "locator_path",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=True,
        ),
        schema=APPLICATION_SCHEMA,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {APPLICATION_SCHEMA}.document_pages
            SET locator_kind = 'page',
                locator_position = page_number,
                locator_path = '[]'::jsonb
            WHERE locator_kind IS NULL
            """
        )
    )
    for column in ("locator_kind", "locator_position", "locator_path"):
        op.alter_column(
            "document_pages",
            column,
            nullable=False,
            schema=APPLICATION_SCHEMA,
        )
    op.create_check_constraint(
        "ck_document_pages_locator_kind",
        "document_pages",
        "locator_kind IN ('page', 'heading', 'slide')",
        schema=APPLICATION_SCHEMA,
    )
    op.create_check_constraint(
        "ck_document_pages_locator_position_positive",
        "document_pages",
        "locator_position > 0",
        schema=APPLICATION_SCHEMA,
    )

    op.drop_constraint(
        "ck_processing_jobs_type",
        "processing_jobs",
        schema=APPLICATION_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_processing_jobs_type",
        "processing_jobs",
        "job_type IN ('pdf_parse', 'document_parse')",
        schema=APPLICATION_SCHEMA,
    )
    op.alter_column(
        "processing_jobs",
        "job_type",
        server_default="document_parse",
        schema=APPLICATION_SCHEMA,
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE {APPLICATION_SCHEMA}.processing_jobs
            SET job_type = 'pdf_parse'
            WHERE job_type = 'document_parse'
            """
        )
    )
    op.drop_constraint(
        "ck_processing_jobs_type",
        "processing_jobs",
        schema=APPLICATION_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_processing_jobs_type",
        "processing_jobs",
        "job_type IN ('pdf_parse')",
        schema=APPLICATION_SCHEMA,
    )
    op.alter_column(
        "processing_jobs",
        "job_type",
        server_default="pdf_parse",
        schema=APPLICATION_SCHEMA,
    )
    op.drop_constraint(
        "ck_document_pages_locator_position_positive",
        "document_pages",
        schema=APPLICATION_SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_document_pages_locator_kind",
        "document_pages",
        schema=APPLICATION_SCHEMA,
        type_="check",
    )
    for column in ("locator_path", "locator_title", "locator_position", "locator_kind"):
        op.drop_column("document_pages", column, schema=APPLICATION_SCHEMA)
