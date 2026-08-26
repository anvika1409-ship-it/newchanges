"""Policy service for routing policies and optimization candidate proposals.

Enforces strict governance rules (AI_DEVELOPMENT_RULES.md section 8,
SECURITY.md section 14):
- Generates policy change proposals with status="PENDING_APPROVAL" and
  requires_approval=True.
- Never directly or autonomously activates a production routing policy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PolicyProposal:
    """A proposed non-activated routing policy adjustment."""

    proposal_id: str
    workload_type: str
    current_model: str
    recommended_model: str
    target_traffic_percent: float
    estimated_monthly_saving_usd: float
    quality_impact_percent: float
    latency_impact_percent: float
    status: str = "PENDING_APPROVAL"
    requires_approval: bool = True
    reason: str = ""


class PolicyService:
    """Service for policy retrieval and candidate proposal generation."""

    def __init__(self) -> None:
        pass

    async def get_active_policy(self, workload_type: str = "predictive_maintenance") -> dict[str, Any]:
        """Retrieve current active routing policy."""
        return {
            "workload_type": workload_type,
            "active_model": "claude-3-5-sonnet" if workload_type == "predictive_maintenance" else "gpt-4o-mini",
            "fallback_model": "gpt-4o-mini",
            "status": "ACTIVE",
            "routing_strategy": "STATIC_PRIMARY",
        }

    async def propose_policy_change(
        self,
        *,
        workload_type: str,
        current_model: str,
        recommended_model: str,
        estimated_monthly_saving: float,
        quality_impact: float = 0.0,
        latency_impact: float = 0.0,
        reason: str = "",
    ) -> PolicyProposal:
        """Create a candidate proposed policy change requiring human approval.

        DO NOT DIRECTLY ACTIVATE A POLICY. This method strictly returns a
        proposal with status="PENDING_APPROVAL" and requires_approval=True.
        """
        proposal_id = f"prop-{uuid.uuid4().hex[:8]}"

        return PolicyProposal(
            proposal_id=proposal_id,
            workload_type=workload_type,
            current_model=current_model,
            recommended_model=recommended_model,
            target_traffic_percent=100.0,
            estimated_monthly_saving_usd=round(estimated_monthly_saving, 2),
            quality_impact_percent=round(quality_impact, 2),
            latency_impact_percent=round(latency_impact, 2),
            status="PENDING_APPROVAL",
            requires_approval=True,
            reason=reason or f"Cost optimization proposal to route {workload_type} to {recommended_model}",
        )
