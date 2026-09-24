from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api import router as auth_router
from app.core.config import get_settings
from app.core.database import close_database, get_db_session, verify_runtime_database_role
from app.core.errors import ApplicationError
from app.core.observability import record_request
from app.core.rate_limit import get_rate_limiter
from app.core.request_limits import RequestBodyLimitMiddleware
from app.documents.api import router as documents_router
from app.jobs.models import WorkerHeartbeatModel
from app.learning.api import router as learning_router
from app.rag.api import router as rag_router

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await verify_runtime_database_role()
    yield
    if get_rate_limiter.cache_info().currsize:
        limiter = get_rate_limiter()
        if limiter is not None:
            await limiter.close()
        get_rate_limiter.cache_clear()
    await close_database()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None if settings.app_env == "production" else "/redoc",
    openapi_url=None if settings.app_env == "production" else "/openapi.json",
)
app.middleware("http")(record_request)
app.add_middleware(
    RequestBodyLimitMiddleware,
    path="/api/v1/documents",
    max_bytes=settings.max_upload_bytes + 1024 * 1024,
)
app.include_router(documents_router)
app.include_router(auth_router)
app.include_router(rag_router)
app.include_router(learning_router)
app.add_middleware(
    RequestBodyLimitMiddleware, path="/api/v1/documents/", max_bytes=16384, prefix=True
)
app.add_middleware(
    RequestBodyLimitMiddleware, path="/api/v1/conversations", max_bytes=16384, prefix=True
)
app.add_middleware(RequestBodyLimitMiddleware, path="/api/v1/quizzes", max_bytes=16384, prefix=True)
app.add_middleware(RequestBodyLimitMiddleware, path="/api/v1/auth/", max_bytes=4096, prefix=True)


@app.exception_handler(ApplicationError)
async def application_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "request_id": request.state.request_id,
                "details": exc.details,
            }
        },
        headers=headers,
    )


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    result = {"name": settings.app_name, "version": settings.app_version}
    if settings.app_env == "development":
        result["docs"] = "/docs"
    return result


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        result = await session.execute(
            text(
                "SELECT current_database(), "
                "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
        )
        database, vector_enabled = result.one()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not ready",
        ) from exc

    if not vector_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pgvector extension is not enabled",
        )

    return {
        "status": "ok",
        "database": database,
        "pgvector": True,
    }


@app.get("/health/worker", tags=["health"])
async def worker_readiness(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        last_seen_at = await session.scalar(
            select(func.max(WorkerHeartbeatModel.last_seen_at)).where(
                WorkerHeartbeatModel.worker_type == "document-parser"
            )
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document worker health is unavailable",
        ) from exc
    if last_seen_at is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document worker has not reported readiness",
        )
    age_seconds = (datetime.now(UTC) - last_seen_at).total_seconds()
    if age_seconds > settings.worker_health_stale_seconds:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document worker heartbeat is stale",
        )
    return {
        "status": "ok",
        "worker": "document-parser",
        "last_seen_at": last_seen_at,
    }
