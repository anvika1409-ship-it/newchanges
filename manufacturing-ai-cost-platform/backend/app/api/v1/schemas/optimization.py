"""API schemas for optimization endpoints.

Matches API_CONTRACT.yaml definitions exactly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
