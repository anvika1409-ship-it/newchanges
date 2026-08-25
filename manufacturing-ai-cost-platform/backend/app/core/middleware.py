"""HTTP middleware: correlation IDs, request sizing and access logging.

Implements the request/correlation ID requirement in AI_DEVELOPMENT_RULES.md
section 18 and the request size limit in SECURITY.md section 18.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.context import bind_correlation, current_correlation, reset_correlation
from app.core.errors import error_payload
from app.core.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"

# Accept only a bounded, printable inbound id so a client cannot inject
# newlines or unbounded data into the log stream via a header.
_MAX_ID_LENGTH = 128


def _clean_inbound_id(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > _MAX_ID_LENGTH:
        return None
    if not all(ch.isalnum() or ch in "-_." for ch in candidate):
        return None
    return candidate


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign a request id and trace id, and echo them on the response.

    An inbound ``X-Request-ID`` is honoured when it is well formed so a caller
    can correlate across systems; otherwise one is generated.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _clean_inbound_id(request.headers.get(REQUEST_ID_HEADER)) or str(
            uuid.uuid4()
        )
        trace_id = _clean_inbound_id(request.headers.get(TRACE_ID_HEADER)) or request_id

        tokens = bind_correlation(request_id=request_id, trace_id=trace_id)
        request.state.request_id = request_id
        request.state.trace_id = trace_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            # Re-raised for the registered exception handlers; logged here so the
            # duration and correlation are not lost.
            logger.exception(
                "request_error",
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers[REQUEST_ID_HEADER] = request_id
            response.headers[TRACE_ID_HEADER] = trace_id
            logger.info(
                "request_completed",
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            return response
        finally:
            reset_correlation(tokens)


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject oversized requests before they are parsed or reach a model.

    Only the declared ``Content-Length`` is checked here. Per-field and
    per-artifact limits belong to the input guardrail layer
    (AI_WORKFLOWS.md section 8), which is not implemented in this scaffold.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                length = -1
            if length > self._max_bytes:
                logger.warning(
                    "request_rejected_oversized",
                    extra={
                        "http_path": request.url.path,
                        "content_length": length,
                        "max_request_bytes": self._max_bytes,
                        "status": 413,
                    },
                )
                return JSONResponse(
                    status_code=413,
                    content=error_payload(
                        "payload_too_large",
                        "Request body exceeds the configured limit",
                        {"max_request_bytes": self._max_bytes},
                    ),
                    headers={
                        REQUEST_ID_HEADER: current_correlation().get("request_id") or ""
                    },
                )
        return await call_next(request)
