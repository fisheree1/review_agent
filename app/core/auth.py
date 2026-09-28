from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.application import AuthService
from app.auth.dependencies import get_auth_service
from app.core.config import Settings, get_settings
from app.core.errors import ApplicationError

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    workspace_public_id: UUID
    user_public_id: UUID | None = None
    email: str | None = None
    csrf_token: str | None = None


def verify_auth_gateway(request: Request, settings: Settings) -> None:
    if settings.auth_gateway_enabled and not secrets.compare_digest(
        request.headers.get("X-Review-Gateway", "").encode("utf-8"),
        settings.auth_gateway_token.get_secret_value().encode("utf-8"),
    ):
        raise ApplicationError(
            code="AUTHENTICATION_REQUIRED", message="请通过门户登录", status_code=401
        )


async def get_current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Principal:
    verify_auth_gateway(request, settings)
    token = request.cookies.get(settings.auth_cookie_name)
    if token:
        identity = await service.current(token)
        if identity is None:
            raise ApplicationError(
                code="AUTHENTICATION_REQUIRED", message="请重新登录", status_code=401
            )
        if settings.auth_gateway_enabled and identity.user_id != settings.auth_gateway_user_id:
            raise ApplicationError(
                code="AUTHENTICATION_REQUIRED", message="账号与门户授权不匹配", status_code=401
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(supplied, identity.csrf_token):
                raise ApplicationError(
                    code="CSRF_INVALID", message="页面验证已失效，请刷新后重试", status_code=403
                )
        return Principal(
            workspace_public_id=identity.workspace_id,
            user_public_id=identity.user_id,
            email=identity.email,
            csrf_token=identity.csrf_token,
        )

    expected_token = settings.local_api_token.get_secret_value()
    supplied_token = credentials.credentials if credentials is not None else ""
    if (
        settings.app_env == "development"
        and credentials is not None
        and credentials.scheme.lower() == "bearer"
        and expected_token
        and settings.local_workspace_id is not None
        and secrets.compare_digest(supplied_token, expected_token)
    ):
        return Principal(workspace_public_id=settings.local_workspace_id)
    raise ApplicationError(code="AUTHENTICATION_REQUIRED", message="请登录后继续", status_code=401)
