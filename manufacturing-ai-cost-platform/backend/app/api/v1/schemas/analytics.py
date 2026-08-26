"""API schemas for forecasts and anomalies endpoints.

Matches API_CONTRACT.yaml definitions exactly.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class PageInfo(BaseModel):
    """Standard pagination metadata (API_CONTRACT.yaml)."""

    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class Forecast(BaseModel):
    """Forecast item matching API_CONTRACT.yaml."""

    id: str
    scope_type: str
    scope_id: str
    forecast_date: date
    predicted_cost: float
    lower_bound: float
    upper_bound: float
    confidence: float
    forecast_model_name: str
    forecast_model_version: str
    provenance: Literal["FORECAST"] = "FORECAST"


class ForecastList(BaseModel):
    """List response for GET /forecasts matching API_CONTRACT.yaml."""

    items: list[Forecast]
    page: PageInfo


class Anomaly(BaseModel):
    """Anomaly item matching API_CONTRACT.yaml."""

    id: str
    timestamp: datetime
    scope_type: str
    scope_id: str
    anomaly_type: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    expected_value: float
    actual_value: float
    deviation_percent: float
    reason: str
    status: str


class AnomalyList(BaseModel):
    """List response for GET /anomalies matching API_CONTRACT.yaml."""

    items: list[Anomaly]
    page: PageInfo
