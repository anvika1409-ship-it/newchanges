"""API schemas for optimization endpoints.

Matches API_CONTRACT.yaml definitions exactly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.schemas.analytics import PageInfo


class ProposedPolicy(BaseModel):
    """Proposed non-activated routing policy draft."""

    policy_id: str
    workload_id: str
    strategy: str
    primary_model: str
    fallback_model: str
    estimated_monthly_saving_usd: float
    status: Literal["PENDING_APPROVAL", "DRAFT"] = "PENDING_APPROVAL"
    requires_approval: bool = True


class OptimizationRecommendation(BaseModel):
    """Optimization recommendation item matching API_CONTRACT.yaml."""

    id: str
    workload_id: str
    current_strategy: str
    recommended_strategy: str
    estimated_saving: float
    estimated_saving_percent: float
    quality_impact_percent: float
    latency_impact_percent: float
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    recommendation_reason: str
    status: Literal["DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "APPLIED", "ROLLED_BACK"]
    provenance: Literal["ESTIMATED", "SIMULATED"] = "ESTIMATED"
    applied_policy_id: str | None = None
    superseded_policy_id: str | None = None
    created_at: datetime
    approved_at: datetime | None = None
    applied_at: datetime | None = None
    rolled_back_at: datetime | None = None
    approved_by: str | None = None


class OptimizationRecommendationList(BaseModel):
    """List response for GET /optimization/recommendations."""

    items: list[OptimizationRecommendation]
    page: PageInfo


class OptimizationAnalyzeRequest(BaseModel):
    """Request body for POST /optimization/analyze matching API_CONTRACT.yaml."""

    workload_id: str
    simulation_only: bool = True
    target_saving_percent: float | None = Field(default=None, ge=0.0, le=100.0)


class OptimizationAnalyzeAccepted(BaseModel):
    """Response body for POST /optimization/analyze matching API_CONTRACT.yaml."""

    request_id: str
    recommendation_id: str
    status: Literal["DRAFT", "PENDING_APPROVAL"] = "PENDING_APPROVAL"


class ApprovalDecision(BaseModel):
    """Request body for POST /optimization/{id}/approve matching API_CONTRACT.yaml."""

    approved: bool = True
    reason: str = ""


class OptimizationApplyRequest(BaseModel):
    """Request body for POST /optimization/{id}/apply matching API_CONTRACT.yaml."""

    activation_mode: Literal["CANARY", "FULL"] = "CANARY"
    canary_traffic_percent: float | None = Field(default=10.0, ge=0.0, le=100.0)
    reason: str = ""


class OptimizationApplyResult(BaseModel):
    """Response body for POST /optimization/{id}/apply matching API_CONTRACT.yaml."""

    recommendation_id: str
    status: Literal["APPLIED"] = "APPLIED"
    applied_policy_id: str
    applied_policy_version: int
    superseded_policy_id: str | None = None
    activation_mode: Literal["CANARY", "FULL"] = "CANARY"
    canary_traffic_percent: float | None = None


class OptimizationRollbackRequest(BaseModel):
    """Request body for POST /optimization/{id}/rollback matching API_CONTRACT.yaml."""

    reason: str = ""


class OptimizationRollbackResult(BaseModel):
    """Response body for POST /optimization/{id}/rollback matching API_CONTRACT.yaml."""

    recommendation_id: str
    status: Literal["ROLLED_BACK"] = "ROLLED_BACK"
    rolled_back_policy_id: str
    reactivated_policy_id: str | None = None
    reactivated_policy_version: int | None = None


# ── What-if simulation (AI_WORKFLOWS.md section 10) ────────────────────────
class ModelMixEntry(BaseModel):
    """One model's share of the proposed routing mix."""

    model_id: str = Field(min_length=1, description="models.id from the registry")
    share_percent: float = Field(ge=0, le=100)


class SimulationRequest(BaseModel):
    """Assumptions to simulate. Inputs match AI_WORKFLOWS.md section 10."""

    model_config = ConfigDict(extra="forbid")

    request_volume: int = Field(ge=0, description="AI requests over the horizon")
    workload_id: str | None = None
    production_volume: int | None = Field(default=None, ge=0)
    image_volume: int | None = Field(default=None, ge=0)
    budget_amount: float | None = Field(default=None, ge=0)
    model_mix: list[ModelMixEntry] = Field(default_factory=list)
    horizon_days: int = Field(default=30, ge=1, le=365)


class SimulationFigure(BaseModel):
    """One cost figure with its provenance attached.

    ``amount`` is null when the figure could not be computed. Null is not zero,
    and a client must not render the two the same way.
    """

    amount: float | None = None
    currency: str | None = None
    provenance: Literal["ACTUAL", "ESTIMATED", "FORECAST", "SIMULATED", "UNAVAILABLE"]


class SimulationResult(BaseModel):
    """A comparison, never a commitment.

    Applying any of it requires the normal recommendation lifecycle
    (SECURITY.md section 15).
    """

    provenance: Literal["SIMULATED"] = "SIMULATED"
    horizon_days: int
    current_cost: SimulationFigure
    forecast_cost: SimulationFigure
    optimized_cost: SimulationFigure
    estimated_saving: SimulationFigure
    estimated_saving_percent: float | None = None
    quality_impact_percent: float | None = None
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    within_budget: bool | None = None
    unpriced_model_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
