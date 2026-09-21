from functools import lru_cache
from typing import Self
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


def build_database_url(
    *,
    username: str,
    password: SecretStr,
    host: str,
    port: int,
    database: str,
) -> str:
    return URL.create(
        drivername="postgresql+asyncpg",
        username=username,
        password=password.get_secret_value(),
        host=host,
        port=port,
        database=database,
    ).render_as_string(hide_password=False)


class EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class DatabaseSettings(EnvironmentSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "review_agent"
    postgres_runtime_user: str = "review_agent_app"
    postgres_runtime_password: SecretStr

    @property
    def sqlalchemy_database_url(self) -> str:
        return build_database_url(
            username=self.postgres_runtime_user,
            password=self.postgres_runtime_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


class StorageSettings(EnvironmentSettings):
    storage_endpoint: str = "localhost:9000"
    storage_access_key: str
    storage_secret_key: SecretStr
    storage_bucket: str = "review-agent-documents"
    storage_secure: bool = False


class StorageAdminSettings(StorageSettings):
    storage_admin_access_key: str
    storage_admin_secret_key: SecretStr


class Settings(DatabaseSettings):
    app_name: str = "Review Agent API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    local_api_token: SecretStr
    local_workspace_id: UUID
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    storage_endpoint: str = "localhost:9000"
    storage_access_key: str
    storage_secret_key: SecretStr
    storage_bucket: str = "review-agent-documents"
    storage_secure: bool = False
    worker_health_stale_seconds: int = Field(default=30, gt=0)


class WorkerSettings(DatabaseSettings):
    storage_endpoint: str = "localhost:9000"
    storage_access_key: str
    storage_secret_key: SecretStr
    storage_bucket: str = "review-agent-documents"
    storage_secure: bool = False
    job_poll_seconds: float = Field(default=1.0, gt=0)
    job_lease_seconds: int = Field(default=120, gt=0)
    pdf_parser_timeout_seconds: int = Field(default=60, gt=0)
    max_pdf_pages: int = Field(default=500, gt=0)
    max_pdf_characters: int = Field(default=5_000_000, gt=0)
    office_parser_timeout_seconds: int = Field(default=60, gt=0)
    max_office_units: int = Field(default=1_000, gt=0)
    max_office_characters: int = Field(default=5_000_000, gt=0)
    max_office_uncompressed_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    worker_heartbeat_seconds: float = Field(default=5.0, gt=0)


class MigrationSettings(EnvironmentSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "review_agent"
    postgres_migrator_user: str = "review_agent_migrator"
    postgres_migrator_password: SecretStr

    @property
    def sqlalchemy_database_url(self) -> str:
        return build_database_url(
            username=self.postgres_migrator_user,
            password=self.postgres_migrator_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


class ProvisioningSettings(EnvironmentSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "review_agent"
    postgres_admin_user: str = "review_agent_admin"
    postgres_admin_password: SecretStr
    postgres_migrator_user: str = "review_agent_migrator"
    postgres_migrator_password: SecretStr
    postgres_runtime_user: str = "review_agent_app"
    postgres_runtime_password: SecretStr

    @model_validator(mode="after")
    def database_roles_are_distinct(self) -> Self:
        roles = {
            self.postgres_admin_user,
            self.postgres_migrator_user,
            self.postgres_runtime_user,
        }
        if len(roles) != 3:
            raise ValueError("PostgreSQL admin, migrator, and runtime roles must be distinct")
        return self


@lru_cache
def get_settings() -> Settings:
    # BaseSettings supplies required secrets from environment sources at runtime.
    return Settings()  # type: ignore[call-arg]


@lru_cache
def get_database_settings() -> DatabaseSettings:
    # BaseSettings supplies required secrets from environment sources at runtime.
    return DatabaseSettings()  # type: ignore[call-arg]
