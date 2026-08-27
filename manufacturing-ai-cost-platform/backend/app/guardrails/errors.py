"""Guardrail rejections.

Every guardrail failure is one of these, so a caller can tell *which* layer
refused without the message revealing why in enough detail to help tune an
attack (SECURITY.md section 18: do not expose internal detail).

Each carries the layer that rejected, which is what gets recorded as
``usage_events.guardrail_decision`` (DATABASE_SCHEMA.md section 14).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.errors import AppError


class GuardrailLayer(StrEnum):
    """The four layers of AI_WORKFLOWS.md section 8."""

    INPUT = "INPUT"
    CONTEXT = "CONTEXT"
    TOOL = "TOOL"
    OUTPUT = "OUTPUT"


#: Recorded when every layer allowed the request through.
ALLOW = "ALLOW"


class GuardrailViolation(AppError):  # noqa: N818 - a violation, not an Error suffix
    """Base class. Always a client-visible refusal, never a 500."""

    status_code = 400
    code = "guardrail_violation"
    message = "Request rejected by a guardrail"
    layer: GuardrailLayer = GuardrailLayer.INPUT

    def __init__(
        self,
        message: str | None = None,
        *,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        #: Short machine-readable reason. Recorded in telemetry and audit, and
        #: deliberately coarser than the internal detail that produced it.
        self.reason = reason or self.code
        merged = {"guardrail_layer": str(self.layer), "reason": self.reason}
        if details:
            merged.update(details)
        super().__init__(message, details=merged)

    @property
    def decision(self) -> str:
        """Value for ``usage_events.guardrail_decision``."""
        return f"{self.layer}:{self.reason}"


# ------------------------------------------------------------------- input
class InputRejected(GuardrailViolation):
    status_code = 422
    code = "input_rejected"
    message = "Request input failed validation"
    layer = GuardrailLayer.INPUT


class PayloadTooLarge(GuardrailViolation):
    status_code = 413
    code = "payload_too_large"
    message = "Request exceeds the configured size limit"
    layer = GuardrailLayer.INPUT


class PromptInjectionSuspected(GuardrailViolation):
    """Untrusted content carried instruction-like text.

    Detection is one layer of several, never the whole defence — SECURITY.md
    section 9 states plainly that no detector is perfect.
    """

    status_code = 422
    code = "prompt_injection_suspected"
    message = "Input contains content that cannot be safely passed to a model"
    layer = GuardrailLayer.INPUT


# ----------------------------------------------------------------- context
class ContextRejected(GuardrailViolation):
    status_code = 403
    code = "context_rejected"
    message = "Retrieved context is not authorized for this caller"
    layer = GuardrailLayer.CONTEXT


class ContextTooLarge(GuardrailViolation):
    status_code = 422
    code = "context_too_large"
    message = "Assembled context exceeds the configured limit"
    layer = GuardrailLayer.CONTEXT


# -------------------------------------------------------------------- tool
class ToolNotRegistered(GuardrailViolation):
    """A model asked for a tool the registry does not contain.

    SECURITY.md section 11: a model cannot call an unregistered tool.
    """

    status_code = 403
    code = "tool_not_registered"
    message = "The requested tool is not registered"
    layer = GuardrailLayer.TOOL


class ToolNotAuthorized(GuardrailViolation):
    status_code = 403
    code = "tool_not_authorized"
    message = "The caller may not use the requested tool"
    layer = GuardrailLayer.TOOL


class ToolParametersInvalid(GuardrailViolation):
    status_code = 422
    code = "tool_parameters_invalid"
    message = "Tool parameters failed server-side validation"
    layer = GuardrailLayer.TOOL


class ToolRequiresApproval(GuardrailViolation):
    """A high-risk tool needs a human decision first.

    202 rather than 403: the request is not refused, it is pending
    (SECURITY.md sections 11 and 14).
    """

    status_code = 202
    code = "tool_requires_approval"
    message = "The requested tool requires human approval"
    layer = GuardrailLayer.TOOL


class ToolCallLimitExceeded(GuardrailViolation):
    status_code = 429
    code = "tool_call_limit_exceeded"
    message = "Tool call limit exceeded for this execution"
    layer = GuardrailLayer.TOOL


# ------------------------------------------------------------------ output
class OutputRejected(GuardrailViolation):
    status_code = 502
    code = "output_rejected"
    message = "Model output failed validation"
    layer = GuardrailLayer.OUTPUT


class SensitiveOutputBlocked(GuardrailViolation):
    """Model output carried something that must not leave the platform."""

    status_code = 502
    code = "sensitive_output_blocked"
    message = "Model output was withheld because it contained sensitive data"
    layer = GuardrailLayer.OUTPUT


class UnsafeActionBlocked(GuardrailViolation):
    """Output asked for an action outside the allowlist.

    SECURITY.md section 12: never execute generated SQL, shell commands or
    privileged operations directly from model output.
    """

    status_code = 502
    code = "unsafe_action_blocked"
    message = "Model output requested an action that is not permitted"
    layer = GuardrailLayer.OUTPUT


# ------------------------------------------------------------------ limits
class TokenLimitExceeded(GuardrailViolation):
    status_code = 422
    code = "token_limit_exceeded"
    message = "Request exceeds the configured token limit"
    layer = GuardrailLayer.CONTEXT


class IterationLimitExceeded(GuardrailViolation):
    """A workflow ran past its bounded step count.

    SECURITY.md section 19: never allow an agent loop to continue indefinitely.
    """

    status_code = 500
    code = "iteration_limit_exceeded"
    message = "Workflow exceeded its iteration limit"
    layer = GuardrailLayer.TOOL
