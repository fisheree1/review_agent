"""Add private generation audit metadata without rewriting existing Quiz records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0012_quiz_agent_usage"
down_revision: str | None = "0011_version_purge_trigger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quizzes", sa.Column("generation_usage", JSONB(), nullable=True), schema=S)


def downgrade() -> None:
    op.drop_column("quizzes", "generation_usage", schema=S)
