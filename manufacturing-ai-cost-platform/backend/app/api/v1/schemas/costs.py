"""API schemas for Cost and Telemetry endpoints.

Matches API_CONTRACT.yaml definitions exactly (lines 1238-1324).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CostSummary(BaseModel):
    """Cost summary matching API_CONTRACT.yaml.

    Actual and estimated spend are reported separately and never summed into
    a single unlabelled figure (AI_DEVELOPMENT_RULES.md sections 41 and 42).
    """

    actual_cost: float = Field(
        default=0.0,
        description="Sum of cost_events with provenance = ACTUAL.",
    )
    estimated_cost: float = Field(
        default=0.0,
        description="Sum of cost_events with provenance = ESTIMATED.",
    )
    unavailable_cost_events: int = Field(
        default=0,
        description="Count of events with provenance = UNAVAILABLE.",
    )
    currency: str = Field(
        default="USD",
        description="Platform base currency used for aggregation.",
    )
    total_requests: int = Field(
        default=0,
        description="Total count of AI execution requests.",
    )
    total_tokens: int = Field(
        default=0,
        description="Total tokens consumed across all requests.",
    )
    average_cost_per_request: float = Field(
        default=0.0,
        description="Average cost per request.",
    )
    budget_consumed_percent: float = Field(
        default=0.0,
        description="Percentage of active budget consumed.",
    )
    forecast_month_end_cost: float = Field(
        default=0.0,
        description="FORECAST value. Must be displayed as a forecast.",
    )


class CostBreakdownItem(BaseModel):
    """Cost breakdown item for a specific dimension."""

    id: str
    name: str
    actual_cost: float = 0.0
    estimated_cost: float = 0.0
    currency: str = "USD"
    total_requests: int = 0
    total_tokens: int = 0


class CostBreakdown(BaseModel):
    """Cost breakdown grouped by dimension (model, agent, plant)."""

    dimension: Literal["model", "agent", "plant"]
    items: list[CostBreakdownItem] = Field(default_factory=list)


class CostTrendPoint(BaseModel):
    """A single data point in a cost time-series trend."""

    bucket_start: datetime
    actual_cost: float = 0.0
    estimated_cost: float = 0.0
    currency: str = "USD"
    total_requests: int = 0
    total_tokens: int = 0


class CostTrend(BaseModel):
    """Historical cost trend series across time buckets."""

    granularity: Literal["hour", "day", "week", "month"] = "day"
    points: list[CostTrendPoint] = Field(default_factory=list)
