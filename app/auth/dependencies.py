from functools import lru_cache

from app.auth.application import AuthService
from app.auth.passwords import Argon2Passwords
from app.auth.store import SqlAuthStore
from app.core.database import async_session_factory


@lru_cache
def get_auth_service() -> AuthService:
    return AuthService(SqlAuthStore(async_session_factory), Argon2Passwords())
