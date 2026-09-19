from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_database_settings

settings = get_database_settings()

engine: AsyncEngine = create_async_engine(
    settings.sqlalchemy_database_url,
    pool_pre_ping=True,
)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


async def close_database() -> None:
    await engine.dispose()


async def verify_runtime_database_role() -> None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        )
        role = result.one_or_none()

    if role is None:
        raise RuntimeError("Application database role does not exist")
    if any(role):
        raise RuntimeError("Application database role has administrative privileges")
