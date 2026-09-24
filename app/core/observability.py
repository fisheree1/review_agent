"""Record HTTP outcomes without URLs, user input, cookies, or exception text."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Awaitable, Callable
from time import monotonic
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("review_agent.http")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


async def record_request(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = str(uuid4())
    request.state.request_id = request_id
    started = monotonic()
    error_type: str | None = None
    try:
        response = await call_next(request)
    except Exception as exc:
        error_type = type(exc).__name__
        response = JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务暂时不可用，请稍后重试",
                    "request_id": request_id,
                    "details": {},
                }
            },
        )
    response.headers["X-Request-ID"] = request_id
    route = request.scope.get("route")
    route_template = getattr(route, "path", "unmatched")
    logger.info(
        json.dumps(
            {
                "event": "http_request",
                "request_id": request_id,
                "method": request.method,
                "route": route_template,
                "status": response.status_code,
                "duration_ms": round((monotonic() - started) * 1000, 1),
                **({"error_type": error_type} if error_type else {}),
            },
            separators=(",", ":"),
        )
    )
    return response
