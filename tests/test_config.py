import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import DatabaseSettings, MigrationSettings, ProvisioningSettings


def test_database_url_safely_encodes_credentials() -> None:
    settings = DatabaseSettings(
        postgres_runtime_user="reader@example.com",
        postgres_runtime_password=SecretStr("unsafe:/password"),
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="review_agent",
    )

    assert settings.sqlalchemy_database_url == (
        "postgresql+asyncpg://reader%40example.com:unsafe%3A%2Fpassword@localhost:5432/review_agent"
    )


def test_migration_url_uses_only_migrator_credentials() -> None:
    settings = MigrationSettings(
        postgres_migrator_user="schema-owner",
        postgres_migrator_password=SecretStr("migration-secret"),
        postgres_host="db",
        postgres_port=5432,
        postgres_db="review_agent",
    )

    assert settings.sqlalchemy_database_url == (
        "postgresql+asyncpg://schema-owner:migration-secret@db:5432/review_agent"
    )


def test_database_provisioning_rejects_reused_privileged_role() -> None:
    with pytest.raises(ValidationError, match="roles must be distinct"):
        ProvisioningSettings(
            postgres_admin_user="shared_role",
            postgres_admin_password=SecretStr("admin-secret"),
            postgres_migrator_user="shared_role",
            postgres_migrator_password=SecretStr("migration-secret"),
            postgres_runtime_user="review_agent_app",
            postgres_runtime_password=SecretStr("runtime-secret"),
        )
