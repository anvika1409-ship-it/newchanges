"""Per-request correlation context.

Holds the identifiers that ARCHITECTURE.md section 15 requires on every request
so they can be attached to logs without threading them through every call.

Telemetry persistence is not implemented in this scaffold; these values are the
correlation inputs it will consume.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)


@dataclass(frozen=True, slots=True)
class CorrelationTokens:
    request_id: Token[str | None]
    trace_id: Token[str | None]
    tenant_id: Token[str | None]
    user_id: Token[str | None]


def bind_correlation(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> CorrelationTokens:
    """Bind correlation identifiers to the current context."""
    return CorrelationTokens(
        request_id=_request_id.set(request_id),
        trace_id=_trace_id.set(trace_id),
        tenant_id=_tenant_id.set(tenant_id),
        user_id=_user_id.set(user_id),
    )


def reset_correlation(tokens: CorrelationTokens) -> None:
    """Restore the previous correlation context."""
    _request_id.reset(tokens.request_id)
    _trace_id.reset(tokens.trace_id)
    _tenant_id.reset(tokens.tenant_id)
    _user_id.reset(tokens.user_id)


def get_request_id() -> str | None:
    return _request_id.get()


def get_trace_id() -> str | None:
    return _trace_id.get()


def get_tenant_id() -> str | None:
    return _tenant_id.get()


def get_user_id() -> str | None:
    return _user_id.get()


def set_tenant_id(tenant_id: str | None) -> None:
    """Set the tenant derived from the authenticated principal.

    Tenant identity is always derived server-side from the authenticated
    context and never from a client-supplied value (SECURITY.md section 5).
    """
    _tenant_id.set(tenant_id)


def set_user_id(user_id: str | None) -> None:
    _user_id.set(user_id)


def current_correlation() -> dict[str, str | None]:
    return {
        "request_id": _request_id.get(),
        "trace_id": _trace_id.get(),
        "tenant_id": _tenant_id.get(),
        "user_id": _user_id.get(),
    }
