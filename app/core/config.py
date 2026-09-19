from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    app_name: str = "Review Agent API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "review_agent"
    postgres_user: str = "review_agent"
    postgres_password: SecretStr

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def sqlalchemy_database_url(self) -> str:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    # BaseSettings supplies required secrets from environment sources at runtime.
    return Settings()  # type: ignore[call-arg]
