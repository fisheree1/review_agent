from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.auth.application import AuthService
from app.auth.dependencies import get_auth_service
from app.core.auth import Principal, get_current_principal
from app.core.config import Settings, get_settings
from app.core.errors import ApplicationError
from app.core.rate_limit import RedisRateLimiter, enforce_rate_limit, get_rate_limiter

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
Service = Annotated[AuthService, Depends(get_auth_service)]
Config = Annotated[Settings, Depends(get_settings)]
Current = Annotated[Principal, Depends(get_current_principal)]
Limiter = Annotated[RedisRateLimiter | None, Depends(get_rate_limiter)]


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class AuthView(BaseModel):
    user_id: UUID
    workspace_id: UUID
    email: str
    csrf_token: str


class AuthConfig(BaseModel):
    signup_enabled: bool


def _check_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("Origin")
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if (
        (origin is not None and origin != settings.auth_public_origin)
        or (fetch_site is not None and fetch_site != "same-origin")
        or (settings.app_env == "production" and origin is None)
    ):
        raise ApplicationError(code="ORIGIN_INVALID", message="请求来源不受信任", status_code=403)


def _set_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        max_age=24 * 60 * 60,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


@router.get("/config", response_model=AuthConfig)
async def auth_config(settings: Config) -> AuthConfig:
    return AuthConfig(signup_enabled=settings.auth_allow_signup)


@router.post("/register", response_model=AuthView, status_code=201)
async def register(
    body: Credentials, request: Request, response: Response, settings: Config, service: Service
) -> AuthView:
    _check_origin(request, settings)
    if not settings.auth_allow_signup:
        raise ApplicationError(
            code="SIGNUP_DISABLED", message="当前未开放自行注册", status_code=403
        )
    await service.register(body.email, body.password)
    issued = await service.login(body.email, body.password)
    _set_cookie(response, issued.token, settings)
    identity = issued.identity
    return AuthView(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        email=identity.email,
        csrf_token=identity.csrf_token,
    )


@router.post("/login", response_model=AuthView)
async def login(
    body: Credentials,
    request: Request,
    response: Response,
    settings: Config,
    service: Service,
    limiter: Limiter,
) -> AuthView:
    _check_origin(request, settings)
    await enforce_rate_limit(limiter, action="login", subject=body.email.strip().casefold())
    issued = await service.login(body.email, body.password)
    _set_cookie(response, issued.token, settings)
    identity = issued.identity
    return AuthView(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        email=identity.email,
        csrf_token=identity.csrf_token,
    )


@router.get("/me", response_model=AuthView)
async def me(principal: Current, response: Response) -> AuthView:
    response.headers["Cache-Control"] = "no-store"
    if principal.user_public_id is None or principal.email is None or principal.csrf_token is None:
        raise ApplicationError(
            code="AUTHENTICATION_REQUIRED", message="请登录后继续", status_code=401
        )
    return AuthView(
        user_id=principal.user_public_id,
        workspace_id=principal.workspace_public_id,
        email=principal.email,
        csrf_token=principal.csrf_token,
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, principal: Current, settings: Config, service: Service
) -> None:
    if principal.user_public_id is None:
        raise ApplicationError(
            code="AUTHENTICATION_REQUIRED", message="请登录后继续", status_code=401
        )
    await service.logout(request.cookies.get(settings.auth_cookie_name, ""))
    response.delete_cookie(settings.auth_cookie_name, path="/")
    response.headers["Cache-Control"] = "no-store"


@router.post("/change-password", status_code=204)
async def change_password(
    body: PasswordChange, response: Response, principal: Current, settings: Config, service: Service
) -> None:
    if principal.user_public_id is None:
        raise ApplicationError(
            code="AUTHENTICATION_REQUIRED", message="请登录后继续", status_code=401
        )
    await service.change_password(
        principal.user_public_id, body.current_password, body.new_password
    )
    response.delete_cookie(settings.auth_cookie_name, path="/")
    response.headers["Cache-Control"] = "no-store"
