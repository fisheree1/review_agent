from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Review Agent API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://review_agent:review_agent@localhost:5432/review_agent"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
