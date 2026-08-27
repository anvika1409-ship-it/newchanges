"""AI and security guardrails.

The four layers of AI_WORKFLOWS.md section 8, in the order they run:

    input   -> before orchestration; schema, size, fields, prompt injection
    context -> before assembling context; authorization, classification, limits
    tool    -> before any tool call; registry allowlist, RBAC, params, approval
    output  -> before anything acts on a result; schema, secrets, allowed actions

Plus ``limits``, which bounds an execution: iterations, tool calls, tokens and
wall-clock duration (SECURITY.md section 19).

Every layer fails closed. A check that cannot be completed refuses rather than
allowing, because the alternative — proceeding when authorization is unknown —
is how unverified data reaches a model.
"""

from app.guardrails.context_guard import (
    ContextDecision,
    ContextFragment,
    ContextGuard,
    DataClassification,
)
from app.guardrails.errors import (
    ALLOW,
    ContextRejected,
    ContextTooLarge,
    GuardrailLayer,
    GuardrailViolation,
    InputRejected,
    IterationLimitExceeded,
    OutputRejected,
    PayloadTooLarge,
    PromptInjectionSuspected,
    SensitiveOutputBlocked,
    TokenLimitExceeded,
    ToolCallLimitExceeded,
    ToolNotAuthorized,
    ToolNotRegistered,
    ToolParametersInvalid,
    ToolRequiresApproval,
    UnsafeActionBlocked,
)
from app.guardrails.input_guard import (
    Content,
    InputGuard,
    TrustedContent,
    TrustLevel,
    UntrustedContent,
    enforce_no_injection,
    scan_for_injection,
)
from app.guardrails.limits import ExecutionBudget, enforce_token_limit
from app.guardrails.output_guard import OutputGuard, contains_secret
from app.guardrails.tool_guard import (
    RegisteredTool,
    ToolCallRequest,
    ToolGuard,
    ToolRisk,
)

__all__ = [
    "ALLOW",
    "Content",
    "ContextDecision",
    "ContextFragment",
    "ContextGuard",
    "ContextRejected",
    "ContextTooLarge",
    "DataClassification",
    "ExecutionBudget",
    "GuardrailLayer",
    "GuardrailViolation",
    "InputGuard",
    "InputRejected",
    "IterationLimitExceeded",
    "OutputGuard",
    "OutputRejected",
    "PayloadTooLarge",
    "PromptInjectionSuspected",
    "RegisteredTool",
    "SensitiveOutputBlocked",
    "TokenLimitExceeded",
    "ToolCallLimitExceeded",
    "ToolCallRequest",
    "ToolGuard",
    "ToolNotAuthorized",
    "ToolNotRegistered",
    "ToolParametersInvalid",
    "ToolRequiresApproval",
    "ToolRisk",
    "TrustLevel",
    "TrustedContent",
    "UnsafeActionBlocked",
    "UntrustedContent",
    "contains_secret",
    "enforce_no_injection",
    "enforce_token_limit",
    "scan_for_injection",
]
