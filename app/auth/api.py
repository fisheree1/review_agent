from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.auth.application import AuthService
from app.auth.dependencies import get_auth_service
from app.core.auth import Principal, get_current_principal, verify_auth_gateway
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
    login_mode: Literal["password", "portal"] = "password"


class AuthConfig(BaseModel):
    signup_enabled: bool
    login_mode: Literal["password", "portal"]


def _require_password_auth(settings: Settings) -> None:
    if settings.auth_gateway_enabled:
        raise ApplicationError(
            code="AUTH_MANAGED_BY_PORTAL", message="请在门户管理登录和密码", status_code=403
        )


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
        path=settings.auth_cookie_path,
    )
    response.headers["Cache-Control"] = "no-store"


@router.get("/config", response_model=AuthConfig)
async def auth_config(settings: Config) -> AuthConfig:
    return AuthConfig(
        signup_enabled=settings.auth_allow_signup,
        login_mode="portal" if settings.auth_gateway_enabled else "password",
    )


@router.post("/register", response_model=AuthView, status_code=201)
async def register(
    body: Credentials, request: Request, response: Response, settings: Config, service: Service
) -> AuthView:
    _require_password_auth(settings)
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
    _require_password_auth(settings)
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
async def me(request: Request, response: Response, settings: Config, service: Service) -> AuthView:
    verify_auth_gateway(request, settings)
    if settings.auth_gateway_enabled:
        token = request.cookies.get(settings.auth_cookie_name, "")
        identity = await service.current(token) if token else None
        if identity is None:
            assert settings.auth_gateway_user_id is not None
            issued = await service.trusted_gateway_session(settings.auth_gateway_user_id)
            identity = issued.identity
            _set_cookie(response, issued.token, settings)
        if identity.user_id != settings.auth_gateway_user_id:
            raise ApplicationError(
                code="AUTHENTICATION_REQUIRED", message="账号与门户授权不匹配", status_code=401
            )
        return AuthView(
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            email=identity.email,
            csrf_token=identity.csrf_token,
            login_mode="portal",
        )
    principal = await get_current_principal(request, None, settings, service)
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
    _require_password_auth(settings)
    if principal.user_public_id is None:
        raise ApplicationError(
            code="AUTHENTICATION_REQUIRED", message="请登录后继续", status_code=401
        )
    await service.logout(request.cookies.get(settings.auth_cookie_name, ""))
    response.delete_cookie(settings.auth_cookie_name, path=settings.auth_cookie_path)
    response.headers["Cache-Control"] = "no-store"


@router.post("/change-password", status_code=204)
async def change_password(
    body: PasswordChange, response: Response, principal: Current, settings: Config, service: Service
) -> None:
    _require_password_auth(settings)
    if principal.user_public_id is None:
        raise ApplicationError(
            code="AUTHENTICATION_REQUIRED", message="请登录后继续", status_code=401
        )
    await service.change_password(
        principal.user_public_id, body.current_password, body.new_password
    )
    response.delete_cookie(settings.auth_cookie_name, path=settings.auth_cookie_path)
    response.headers["Cache-Control"] = "no-store"
