"""Error types and global exception handling.

Every error response uses the ``Error`` schema defined in API_CONTRACT.yaml:

    {"code": str, "message": str, "request_id": str | null, "details": object | null}

Internal exception detail never reaches the client (SECURITY.md section 18,
AI_DEVELOPMENT_RULES.md sections 18 and 26). Failures are logged, classified and
normalised — never silently swallowed.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_request_id
from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for errors that are safe to surface to a client."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"
    message = "Invalid request"


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    message = "Authentication required"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "Authorization failed"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found or not visible to the caller"


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    code = "payload_too_large"
    message = "Request body exceeds the configured limit"


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Rate limit exceeded"


class PolicyConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "policy_conflict"
    message = "Policy or budget prevents requested operation"


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "Service is not ready"


class GatewayError(AppError):
    """Normalised failure from an external model provider.

    Provider-specific exceptions are translated here so business logic never
    depends on a vendor SDK's error types (AI_DEVELOPMENT_RULES.md section 4.4).
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "model_gateway_error"
    message = "Model gateway request failed"


def error_payload(
    code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a response body matching the contract's ``Error`` schema."""
    return {
        "code": code,
        "message": message,
        "request_id": get_request_id(),
        "details": details,
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers so no exception escapes as an unformatted 500."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "request_failed",
            extra={"error_code": exc.code, "status": exc.status_code},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Field-level detail is safe and useful; input values are not echoed.
        details = {
            "errors": [
                {"location": list(err.get("loc", [])), "message": err.get("msg", "")}
                for err in exc.errors()
            ]
        }
        logger.info("request_validation_failed", extra={"status": 422})
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload("validation_error", "Request validation failed", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _CODE_BY_STATUS.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else code
        logger.info("http_exception", extra={"status": exc.status_code, "error_code": code})
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, message),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Logged with the exception attached; the client sees a generic message.
        logger.exception("unhandled_exception", exc_info=exc, extra={"status": 500})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_payload("internal_error", "Internal server error"),
        )


_CODE_BY_STATUS: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "policy_conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "model_gateway_error",
    503: "service_unavailable",
}
