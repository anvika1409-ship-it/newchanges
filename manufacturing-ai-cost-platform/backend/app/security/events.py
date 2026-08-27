"""Security event vocabulary.

SECURITY.md section 20 requires authentication failures, authorization failures
and tenant-isolation violations to be observable. Emitting them through one
helper gives monitoring a single, stable event name to alert on instead of a
scatter of ad-hoc log lines.

Nothing here ever receives a token, a header or a request body. The fields are
identifiers and decision metadata only (AI_DEVELOPMENT_RULES.md section 27).
The redaction filter in ``app.core.logging`` is a backstop, not a licence to
pass secrets in.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class SecurityEvent(StrEnum):
    """Names monitoring can alert on. Values are stable; treat them as an API."""

    AUTHENTICATION_FAILED = "security.authentication_failed"
    AUTHORIZATION_DENIED = "security.authorization_denied"
    TENANT_ISOLATION_VIOLATION = "security.tenant_isolation_violation"
    ROUTE_UNPROTECTED = "security.route_unprotected"

    # Guardrail events. SECURITY.md section 20 requires prompt-injection
    # detections, tool denials and rate-limit events to be tracked.
    PROMPT_INJECTION_DETECTED = "security.prompt_injection_detected"
    GUARDRAIL_REJECTED = "security.guardrail_rejected"
    TOOL_DENIED = "security.tool_denied"
    SENSITIVE_OUTPUT_BLOCKED = "security.sensitive_output_blocked"
    REQUEST_REJECTED_OVERSIZED = "security.request_rejected_oversized"
    RATE_LIMIT_EXCEEDED = "security.rate_limit_exceeded"
    HIGH_RISK_APPROVAL_REQUIRED = "security.high_risk_approval_required"


# Field names that must never carry a value into a log record, whatever the
# caller believes they hold.
_FORBIDDEN_FIELDS = frozenset({"token", "authorization", "credentials", "secret", "password"})


def record_security_event(
    event: SecurityEvent,
    *,
    reason: str,
    **fields: Any,
) -> None:
    """Emit one security event.

    Args:
        event: the stable event name.
        reason: short machine-readable cause, e.g. ``"role_not_held"``. Never a
            message containing caller-supplied text.
        **fields: identifiers and decision metadata. ``None`` values are
            dropped so the log stays sparse.
    """
    payload: dict[str, Any] = {"security_event": str(event), "reason": reason}
    for key, value in fields.items():
        if key in _FORBIDDEN_FIELDS:
            # A programming error, not a runtime condition. Drop it rather than
            # raise: refusing to log must not turn into refusing to serve.
            continue
        if value is not None:
            payload[key] = value

    logger.warning(str(event), extra=payload)
