"""The execution plan.

The decision record produced before any model is invoked. ARCHITECTURE.md
section 4 requires the plan to exist before execution, not to be reconstructed
afterwards — that is what makes a routing decision auditable and replayable.

Field names match ``ExecutionPlan`` in API_CONTRACT.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.orchestrator.classification import BusinessPriority, Complexity, RiskLevel
from app.policies.budget_policy import PolicyOutcome


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """What the orchestrator decided, and why."""

    request_id: str
    workload_type: str
    complexity: Complexity
    business_priority: BusinessPriority
    risk_level: RiskLevel
    budget_status: PolicyOutcome

    #: models.id — None only when the plan is BLOCK and nothing was selected.
    selected_model_id: str | None = None
    #: The provider-facing identifier, needed by the gateway. Distinct from
    #: selected_model_id, which is the registry primary key.
    selected_model_name: str | None = None
    #: agents.id
    selected_agent_id: str | None = None

    #: ESTIMATED, never ACTUAL. Computed before execution from registry pricing,
    #: and None when pricing is unknown — an unknown price is not zero
    #: (AI_DEVELOPMENT_RULES.md sections 10 and 41).
    estimated_cost: float | None = None
    estimated_cost_currency: str | None = None

    max_context_tokens: int | None = None
    max_tool_calls: int | None = None

    #: routing_policies.version, or None when no policy was configured and the
    #: deterministic fallback ordering was used.
    routing_policy_version: int | None = None

    tenant_id: str | None = None
    plant_id: str | None = None
    department_id: str | None = None
    workload_id: str | None = None
    trace_id: str | None = None

    #: Why the plan looks the way it does. Human-readable, no secrets.
    decisions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_executable(self) -> bool:
        """Whether this plan may proceed to a model call."""
        return (
            self.budget_status
            not in (PolicyOutcome.BLOCK, PolicyOutcome.REQUIRE_APPROVAL)
            and self.selected_model_name is not None
        )

    def to_contract_dict(self) -> dict[str, Any]:
        """Serialize to the contract's ``ExecutionPlan`` shape."""
        return {
            "workload_type": self.workload_type,
            "complexity": str(self.complexity),
            "selected_model_id": self.selected_model_id,
            "selected_agent_id": self.selected_agent_id,
            "estimated_cost": self.estimated_cost,
            "max_context_tokens": self.max_context_tokens,
            "max_tool_calls": self.max_tool_calls,
            "routing_policy_version": self.routing_policy_version,
            "budget_status": str(self.budget_status),
            "risk_level": str(self.risk_level),
        }
