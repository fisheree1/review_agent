from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
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
        secrets_dir="/run/secrets" if Path("/run/secrets").is_dir() else None,
        extra="ignore",
        hide_input_in_errors=True,
    )


class ModelSettings(EnvironmentSettings):
    deepseek_api_key: SecretStr = SecretStr("")
    dashscope_api_key: SecretStr = SecretStr("")
    dashscope_embedding_url: str = ""
    deepseek_model: str = "deepseek-flash"
    dashscope_model: str = "qwen3.7-text-embedding"

    @field_validator("dashscope_embedding_url")
    @classmethod
    def validate_dashscope_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".maas.aliyuncs.com")
            or parsed.path != "/api/v1/services/embeddings/text-embedding/text-embedding"
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("DASHSCOPE_EMBEDDING_URL must be a Model Studio embedding endpoint")
        return value


class RagSettings(EnvironmentSettings):
    dashscope_model: str = "qwen3.7-text-embedding"

    @property
    def profile(self) -> str:
        return f"dashscope:{self.dashscope_model}:1024:source-window-1500-180-v1"


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
    app_env: Literal["development", "production"] = "development"
    auth_allow_signup: bool = False
    auth_public_origin: str = "http://127.0.0.1:5173"
    local_api_token: SecretStr = SecretStr("")
    local_workspace_id: UUID | None = None
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    storage_endpoint: str = "localhost:9000"
    storage_access_key: str
    storage_secret_key: SecretStr
    storage_bucket: str = "review-agent-documents"
    storage_secure: bool = False
    redis_enabled: bool = False
    redis_host: str = "localhost"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password: SecretStr = SecretStr("")
    worker_health_stale_seconds: int = Field(default=30, gt=0)

    @model_validator(mode="after")
    def validate_public_auth_origin(self) -> Self:
        origin = urlsplit(self.auth_public_origin)
        if (
            origin.scheme not in ("http", "https")
            or not origin.hostname
            or origin.path
            or origin.username is not None
            or origin.password is not None
            or origin.query
            or origin.fragment
            or (self.app_env == "production" and origin.scheme != "https")
        ):
            raise ValueError("AUTH_PUBLIC_ORIGIN must be a valid HTTPS origin in production")
        if self.app_env == "production" and (
            not self.redis_enabled or not self.redis_password.get_secret_value()
        ):
            raise ValueError("Production requires authenticated Redis rate limiting")
        return self

    @property
    def auth_cookie_name(self) -> str:
        if self.app_env == "production":
            return "__Host-review_agent_session"
        return "review_agent_session"


class WorkerSettings(DatabaseSettings):
    storage_endpoint: str = "localhost:9000"
    storage_access_key: str
    storage_secret_key: SecretStr
    storage_bucket: str = "review-agent-documents"
    storage_secure: bool = False
    job_poll_seconds: float = Field(default=1.0, gt=0)
    job_lease_seconds: int = Field(default=120, gt=0)
    pdf_parser_timeout_seconds: int = Field(default=300, gt=0)
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
