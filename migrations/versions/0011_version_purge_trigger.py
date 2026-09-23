"""Purge learning records only when a document version is deleted."""

from collections.abc import Sequence

from alembic import op

from app.core.database_schema import APPLICATION_SCHEMA as S

revision: str = "0011_version_purge_trigger"
down_revision: str | None = "0010_user_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for association in ("conversation_documents", "quiz_documents"):
        op.execute(f"DROP TRIGGER purge_{association}_on_version_delete ON {S}.{association}")
        op.execute(f"DROP FUNCTION {S}.purge_{association}()")
    op.execute(f"""
        CREATE FUNCTION {S}.purge_learning_on_version_delete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            DELETE FROM {S}.conversations WHERE id IN (
                SELECT conversation_id FROM {S}.conversation_documents
                WHERE document_version_id = OLD.id
            );
            DELETE FROM {S}.quizzes WHERE id IN (
                SELECT quiz_id FROM {S}.quiz_documents
                WHERE document_version_id = OLD.id
            );
            RETURN OLD;
        END $$
    """)
    op.execute(f"""
        CREATE TRIGGER purge_learning_on_version_delete
        BEFORE DELETE ON {S}.document_versions
        FOR EACH ROW EXECUTE FUNCTION {S}.purge_learning_on_version_delete()
    """)


def downgrade() -> None:
    # Destructive behavior in the old trigger: production rollback must roll forward.
    op.execute(f"DROP TRIGGER purge_learning_on_version_delete ON {S}.document_versions")
    op.execute(f"DROP FUNCTION {S}.purge_learning_on_version_delete()")
    for association, parent, key in (
        ("conversation_documents", "conversations", "conversation_id"),
        ("quiz_documents", "quizzes", "quiz_id"),
    ):
        op.execute(f"""
            CREATE FUNCTION {S}.purge_{association}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                DELETE FROM {S}.{parent} WHERE id = OLD.{key};
                RETURN OLD;
            END $$
        """)
        op.execute(f"""
            CREATE TRIGGER purge_{association}_on_version_delete
            AFTER DELETE ON {S}.{association}
            FOR EACH ROW EXECUTE FUNCTION {S}.purge_{association}()
        """)
