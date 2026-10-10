"""Store CJK-aware lexical terms for chunks and index them for full-text search.

``to_tsvector('simple', content)`` cannot segment Chinese, so lexical retrieval matched almost
nothing for Chinese questions and every query recomputed vectors without an index. Chunks now
keep Python-tokenized terms (``app.rag.lexical``) in ``search_text`` with a GIN expression
index. Existing chunks are backfilled in id order; no embedding or model call is involved.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.database_schema import APPLICATION_SCHEMA as S
from app.rag.lexical import lexical_document

revision: str = "0018_chunk_search_terms"
down_revision: str | None = "0017_spaced_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCH_SIZE = 500


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("search_text", sa.Text(), nullable=True), schema=S)

    bind = op.get_bind()
    select_batch = sa.text(
        f"SELECT id, content FROM {S}.document_chunks "
        "WHERE id > :after AND search_text IS NULL ORDER BY id LIMIT :limit"
    )
    update_row = sa.text(f"UPDATE {S}.document_chunks SET search_text = :terms WHERE id = :id")
    after = 0
    while True:
        rows = bind.execute(select_batch, {"after": after, "limit": BATCH_SIZE}).all()
        if not rows:
            break
        bind.execute(
            update_row,
            [{"id": row.id, "terms": lexical_document(row.content)} for row in rows],
        )
        after = rows[-1].id

    op.create_index(
        "ix_document_chunks_search",
        "document_chunks",
        [sa.text("to_tsvector('simple'::regconfig, search_text)")],
        postgresql_using="gin",
        schema=S,
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_search", table_name="document_chunks", schema=S)
    op.drop_column("document_chunks", "search_text", schema=S)
