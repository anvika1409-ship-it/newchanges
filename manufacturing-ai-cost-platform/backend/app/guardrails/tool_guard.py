"""Tool guardrails (SECURITY.md section 11).

"A model cannot call an unregistered tool." Everything here follows from that:
the registry in the ``tools`` table is the allowlist, and a name the model
produced is only ever a *lookup key* — never something to execute.

Five checks, all server-side, in order:

    1. registered   — the tool exists in the registry
    2. enabled      — it has not been switched off
    3. authorized   — the caller's role and this workload may use it
    4. parameters   — validated against the tool's own schema
    5. approval     — a high-risk tool needs a human decision first

The order matters: an unregistered tool is refused before its parameters are
inspected, so a probe learns nothing about a tool it cannot reach.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger
from app.guardrails.errors import (
    ToolNotAuthorized,
    ToolNotRegistered,
    ToolParametersInvalid,
    ToolRequiresApproval,
)
from app.security.events import SecurityEvent, record_security_event
from app.security.principal import Principal, Role

logger = get_logger(__name__)


class ToolRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


#: Risk levels that require a human decision before the tool runs
#: (SECURITY.md sections 11 and 14).
_REQUIRES_APPROVAL = frozenset({ToolRisk.HIGH, ToolRisk.CRITICAL})


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """A tool call the model asked for.

    ``name`` is untrusted: it came out of a model, possibly under the influence
    of injected content. It is used only to look up a registry row.
    """

    name: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """A registry row, decoded.

    Mirrors the ``tools`` table (DATABASE_SCHEMA.md section 11.1).
    """

    id: str
    name: str
    allowed_roles: frozenset[Role]
    allowed_workloads: frozenset[str] | None
    risk_level: ToolRisk
    enabled: bool
    estimated_cost: float | None = None
    #: Parameter names the tool accepts. None means "not declared", which is
    #: treated as "accepts nothing" rather than "accepts anything".
    parameter_names: frozenset[str] | None = None

    @classmethod
    def from_record(cls, record: Any) -> RegisteredTool:
        """Decode a ``tools`` row.

        ``allowed_roles`` and ``allowed_workloads`` are stored as serialized
        lists. A role string that does not match a known role is dropped, not
        ignored-and-allowed — an unrecognised entry must never widen access.
        """
        roles: set[Role] = set()
        for raw in _decode_list(record.allowed_roles):
            try:
                roles.add(Role(raw.strip().upper()))
            except ValueError:
                logger.warning(
                    "tool_registry_unknown_role",
                    extra={"tool": getattr(record, "name", None), "role": raw},
                )

        raw_workloads = _decode_list(getattr(record, "allowed_workloads", None))
        try:
            risk = ToolRisk(str(record.risk_level).strip().upper())
        except ValueError:
            # An unrecognised risk level is treated as the most restrictive
            # value, not the least.
            logger.warning(
                "tool_registry_unknown_risk_level",
                extra={"tool": getattr(record, "name", None)},
            )
            risk = ToolRisk.CRITICAL

        return cls(
            id=record.id,
            name=record.name,
            allowed_roles=frozenset(roles),
            allowed_workloads=frozenset(raw_workloads) if raw_workloads else None,
            risk_level=risk,
            enabled=bool(record.enabled),
            estimated_cost=getattr(record, "estimated_cost", None),
        )


def _decode_list(raw: Any) -> list[str]:
    """Decode a serialized list column, tolerating JSON or comma separation."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            return [str(item) for item in parsed] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return [part.strip() for part in text.split(",") if part.strip()]


@dataclass(frozen=True, slots=True)
class ToolAuthorization:
    """Outcome of authorizing one tool call."""

    tool: RegisteredTool
    requires_approval: bool


class ToolGuard:
    """Authorizes tool calls against the registry."""

    def __init__(self, registry: dict[str, RegisteredTool]) -> None:
        #: Keyed by tool name, as the model refers to them.
        self._registry = registry

    def authorize(
        self,
        call: ToolCallRequest,
        principal: Principal,
        *,
        workload_type: str | None = None,
        approved_tool_ids: frozenset[str] = frozenset(),
    ) -> ToolAuthorization:
        """Authorize one tool call, or refuse it.

        Args:
            approved_tool_ids: tools for which a human approval already exists
                for this execution. Supplied by the caller from the ``approvals``
                table — never inferred from the model's own output.

        Raises:
            ToolNotRegistered / ToolNotAuthorized / ToolParametersInvalid /
            ToolRequiresApproval
        """
        # 1. registered
        tool = self._registry.get(call.name)
        if tool is None:
            self._deny(call.name, principal, "not_registered")
            raise ToolNotRegistered(reason="not_registered")

        # 2. enabled
        if not tool.enabled:
            self._deny(call.name, principal, "disabled")
            # Reported as not registered: whether a tool exists but is disabled
            # is not something an unauthorized caller needs to learn.
            raise ToolNotRegistered(reason="disabled")

        # 3. authorized — role, then workload
        if not principal.has_role(*tool.allowed_roles):
            self._deny(call.name, principal, "role_not_permitted")
            raise ToolNotAuthorized(reason="role_not_permitted")

        if (
            tool.allowed_workloads is not None
            and workload_type is not None
            and workload_type not in tool.allowed_workloads
        ):
            self._deny(call.name, principal, "workload_not_permitted")
            raise ToolNotAuthorized(reason="workload_not_permitted")

        # 4. parameters
        self._validate_parameters(tool, call)

        # 5. approval
        requires_approval = tool.risk_level in _REQUIRES_APPROVAL
        if requires_approval and tool.id not in approved_tool_ids:
            record_security_event(
                SecurityEvent.HIGH_RISK_APPROVAL_REQUIRED,
                reason="high_risk_tool",
                tool=tool.name,
                risk_level=str(tool.risk_level),
            )
            raise ToolRequiresApproval(
                reason="high_risk_tool",
                details={"tool": tool.name, "risk_level": str(tool.risk_level)},
            )

        return ToolAuthorization(tool=tool, requires_approval=requires_approval)

    @staticmethod
    def _validate_parameters(tool: RegisteredTool, call: ToolCallRequest) -> None:
        """Validate parameters server-side.

        A tool that declares no parameter names accepts none. Treating an
        undeclared schema as "anything goes" would make the check decorative.
        """
        declared = tool.parameter_names if tool.parameter_names is not None else frozenset()
        unexpected = set(call.parameters) - set(declared)
        if unexpected:
            raise ToolParametersInvalid(
                reason="unexpected_parameters",
                details={"unexpected": sorted(unexpected)},
            )

    @staticmethod
    def _deny(tool_name: str, principal: Principal, reason: str) -> None:
        record_security_event(
            SecurityEvent.TOOL_DENIED,
            reason=reason,
            tool=tool_name,
            held_roles=sorted(str(r) for r in principal.roles),
        )
