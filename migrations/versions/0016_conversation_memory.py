"""Keep bounded conversation memory tied to the exact source snapshot."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0016_conversation_memory"
down_revision: str | None = "0015_learning_organization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("memory", JSONB(), nullable=True), schema=S)


def downgrade() -> None:
    # Optional projection; removing it does not remove messages or cited answers.
    op.drop_column("conversations", "memory", schema=S)
