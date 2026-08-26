"""Normalized model gateway exceptions.

Business logic must never catch a vendor SDK exception
(AI_DEVELOPMENT_RULES.md section 4.4). Every provider failure is translated
here into one of these types, so swapping providers changes nothing upstream.

Each error carries ``retryable``, which is the single source of truth for the
retry policy in ``client.py``. Retrying a 401 or a malformed request wastes
budget and never succeeds, so those are explicitly not retryable.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError


class ModelGatewayError(AppError):
    """Base class for every model gateway failure."""

    status_code = 502
    code = "model_gateway_error"
    message = "Model gateway request failed"

    #: Whether the client may retry this failure.
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        provider: str | None = None,
        operation: str | None = None,
        model: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = {}
        if provider:
            merged["provider"] = provider
        if operation:
            merged["operation"] = operation
        # The model name is safe to surface: it is registry metadata, not a secret.
        if model:
            merged["model"] = model
        if details:
            merged.update(details)

        self.provider = provider
        self.operation = operation
        self.model = model
        super().__init__(message, details=merged or None)


# --------------------------------------------------------------- retryable
class GatewayTimeoutError(ModelGatewayError):
    """The provider did not respond within the configured timeout."""

    status_code = 504
    code = "model_gateway_timeout"
    message = "Model gateway request timed out"
    retryable = True


class GatewayUnavailableError(ModelGatewayError):
    """Connection failure or a 5xx from the provider."""

    status_code = 502
    code = "model_gateway_unavailable"
    message = "Model gateway is unavailable"
    retryable = True


class GatewayRateLimitError(ModelGatewayError):
    """The provider rejected the call for rate or quota reasons."""

    status_code = 429
    code = "model_gateway_rate_limited"
    message = "Model gateway rate limit exceeded"
    retryable = True

    def __init__(self, *args: Any, retry_after_seconds: float | None = None, **kwargs: Any) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(*args, **kwargs)


# ----------------------------------------------------------- not retryable
class GatewayAuthenticationError(ModelGatewayError):
    """Credentials were rejected.

    Never retried: a bad key stays bad, and repeated attempts can trip lockouts.
    The key itself is never included in the message or details.
    """

    status_code = 502
    code = "model_gateway_authentication_failed"
    message = "Model gateway rejected the configured credentials"
    retryable = False


class GatewayBadRequestError(ModelGatewayError):
    """The provider rejected the request as invalid.

    A caller-side defect — an unsupported parameter, an oversized context, a
    model that does not accept the supplied modality. Retrying repeats the same
    mistake at the same cost.
    """

    status_code = 502
    code = "model_gateway_bad_request"
    message = "Model gateway rejected the request"
    retryable = False


class GatewayResponseError(ModelGatewayError):
    """The response did not contain what the operation requires.

    Raised rather than guessing. Fabricating a field the provider did not return
    would corrupt downstream cost and quality data
    (AI_DEVELOPMENT_RULES.md section 10).
    """

    status_code = 502
    code = "model_gateway_invalid_response"
    message = "Model gateway returned an unusable response"
    retryable = False


class CircuitOpenError(ModelGatewayError):
    """The circuit breaker is open; the call was not attempted.

    Fails fast to stop a failing provider from consuming latency budget and to
    give it room to recover.
    """

    status_code = 503
    code = "model_gateway_circuit_open"
    message = "Model gateway is temporarily unavailable (circuit open)"
    retryable = False


class ModelCapabilityError(ModelGatewayError):
    """The requested operation is not supported by this gateway.

    Capability comes from the model registry (ARCHITECTURE.md section 8); the
    gateway refuses rather than assuming a model supports a modality.
    """

    status_code = 400
    code = "model_capability_unsupported"
    message = "The requested capability is not supported"
    retryable = False
