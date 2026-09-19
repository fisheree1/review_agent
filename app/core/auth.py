from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.errors import ApplicationError

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    workspace_public_id: UUID


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    expected_token = settings.local_api_token.get_secret_value()
    supplied_token = credentials.credentials if credentials is not None else ""
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(supplied_token, expected_token)
    ):
        raise ApplicationError(
            code="AUTHENTICATION_REQUIRED",
            message="需要有效的访问令牌",
            status_code=401,
        )
    return Principal(workspace_public_id=settings.local_workspace_id)
