from pydantic import SecretStr

from app.core.config import Settings


def test_database_url_safely_encodes_credentials() -> None:
    settings = Settings(
        postgres_user="reader@example.com",
        postgres_password=SecretStr("unsafe:/password"),
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="review_agent",
    )

    assert settings.sqlalchemy_database_url == (
        "postgresql+asyncpg://reader%40example.com:unsafe%3A%2Fpassword@localhost:5432/review_agent"
    )
