"""Cost response schemas.

Mirrors ``CostSummary``, ``CostBreakdown``, ``CostBreakdownItem``, ``CostTrend``
and ``CostTrendPoint`` in API_CONTRACT.yaml field for field. SQLAlchemy entities
never cross the API boundary (AI_DEVELOPMENT_RULES.md section 16).

Actual and estimated spend are separate fields at every level, and
``unavailable_cost_events`` reports executions whose cost could not be computed.
A client is therefore never handed a single number that silently blends
confirmed charges, estimates and unknowns (AI_DEVELOPMENT_RULES.md sections 41
and 42).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.services.cost_aggregation import (
    CostBreakdownEntry,
    CostSummaryResult,
    CostTrendEntry,
)


class CostSummaryResponse(BaseModel):
    """Contract schema ``CostSummary``."""

    model_config = ConfigDict(frozen=True)

    actual_cost: float
    estimated_cost: float
    unavailable_cost_events: int
    currency: str
    total_requests: int
    total_tokens: int
    average_cost_per_request: float | None = None
    budget_consumed_percent: float | None = None
    #: FORECAST value. Null until the intelligence layer implements forecasting
    #: — a straight-line extrapolation presented in this field would be read as
    #: the platform's forecast (AI_DEVELOPMENT_RULES.md sections 41 and 42).
    forecast_month_end_cost: float | None = None

    @classmethod
    def from_result(
        cls,
        result: CostSummaryResult,
        *,
        budget_consumed_percent: float | None = None,
    ) -> CostSummaryResponse:
        return cls(
            actual_cost=result.actual_cost,
            estimated_cost=result.estimated_cost,
            unavailable_cost_events=result.unavailable_cost_events,
            currency=result.currency,
            total_requests=result.total_requests,
            total_tokens=result.total_tokens,
            average_cost_per_request=result.average_cost_per_request,
            budget_consumed_percent=budget_consumed_percent,
            forecast_month_end_cost=None,
        )


class CostBreakdownItemResponse(BaseModel):
    """Contract schema ``CostBreakdownItem``."""

    model_config = ConfigDict(frozen=True)

    id: str | None = None
    name: str | None = None
    actual_cost: float
    estimated_cost: float
    currency: str
    total_requests: int
    total_tokens: int

    @classmethod
    def from_entry(cls, entry: CostBreakdownEntry) -> CostBreakdownItemResponse:
        return cls(
            id=entry.id,
            name=entry.name,
            actual_cost=entry.actual_cost,
            estimated_cost=entry.estimated_cost,
            currency=entry.currency,
            total_requests=entry.total_requests,
            total_tokens=entry.total_tokens,
        )


class CostBreakdownResponse(BaseModel):
    """Contract schema ``CostBreakdown``."""

    model_config = ConfigDict(frozen=True)

    dimension: str
    items: list[CostBreakdownItemResponse] = Field(default_factory=list)


class CostTrendPointResponse(BaseModel):
    """Contract schema ``CostTrendPoint``."""

    model_config = ConfigDict(frozen=True)

    bucket_start: str
    actual_cost: float
    estimated_cost: float
    currency: str
    total_requests: int
    total_tokens: int

    @classmethod
    def from_entry(cls, entry: CostTrendEntry) -> CostTrendPointResponse:
        return cls(
            bucket_start=entry.bucket_start,
            actual_cost=entry.actual_cost,
            estimated_cost=entry.estimated_cost,
            currency=entry.currency,
            total_requests=entry.total_requests,
            total_tokens=entry.total_tokens,
        )


class CostTrendResponse(BaseModel):
    """Contract schema ``CostTrend``."""

    model_config = ConfigDict(frozen=True)

    granularity: str
    points: list[CostTrendPointResponse] = Field(default_factory=list)
