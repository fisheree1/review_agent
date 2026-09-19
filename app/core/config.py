from functools import lru_cache
from typing import Self

from pydantic import SecretStr, model_validator
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


class Settings(BaseSettings):
    app_name: str = "Review Agent API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "review_agent"
    postgres_runtime_user: str = "review_agent_app"
    postgres_runtime_password: SecretStr

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def sqlalchemy_database_url(self) -> str:
        return build_database_url(
            username=self.postgres_runtime_user,
            password=self.postgres_runtime_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


class MigrationSettings(BaseSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "review_agent"
    postgres_migrator_user: str = "review_agent_migrator"
    postgres_migrator_password: SecretStr

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def sqlalchemy_database_url(self) -> str:
        return build_database_url(
            username=self.postgres_migrator_user,
            password=self.postgres_migrator_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


class ProvisioningSettings(BaseSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "review_agent"
    postgres_admin_user: str = "review_agent_admin"
    postgres_admin_password: SecretStr
    postgres_migrator_user: str = "review_agent_migrator"
    postgres_migrator_password: SecretStr
    postgres_runtime_user: str = "review_agent_app"
    postgres_runtime_password: SecretStr

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
