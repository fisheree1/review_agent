import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import DatabaseSettings, MigrationSettings, ProvisioningSettings, Settings


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


@pytest.mark.parametrize(
    ("app_env", "auth_public_origin"),
    [
        ("prod", "https://example.com"),
        ("production", "http://example.com"),
        ("production", "https://example.com/"),
        ("production", "https://user@example.com"),
    ],
)
def test_invalid_production_auth_configuration_fails_closed(
    app_env: str, auth_public_origin: str
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env=app_env,
            auth_public_origin=auth_public_origin,
            postgres_runtime_password=SecretStr("runtime-secret"),
            storage_access_key="storage-user",
            storage_secret_key=SecretStr("storage-secret"),
        )


def test_production_auth_uses_host_cookie() -> None:
    settings = Settings(
        app_env="production",
        auth_public_origin="https://study.example.com",
        postgres_runtime_password=SecretStr("runtime-secret"),
        storage_access_key="storage-user",
        storage_secret_key=SecretStr("storage-secret"),
        redis_enabled=True,
        redis_password=SecretStr("redis-secret"),
    )
    assert settings.auth_cookie_name == "__Host-review_agent_session"


def test_production_rejects_missing_redis_limit_store() -> None:
    with pytest.raises(ValidationError, match="authenticated Redis rate limiting"):
        Settings(
            app_env="production",
            auth_public_origin="https://study.example.com",
            postgres_runtime_password=SecretStr("runtime-secret"),
            storage_access_key="storage-user",
            storage_secret_key=SecretStr("storage-secret"),
        )
