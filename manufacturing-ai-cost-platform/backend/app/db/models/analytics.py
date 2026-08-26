"""ORM mappings for forecasts and anomalies tables.

Implements DATABASE_SCHEMA.md sections 16 and 17:
- forecasts: Stores forecasted AI costs across scopes and horizons.
  Every record represents a prediction and must carry provenance FORECAST.
- anomalies: Stores detected metric, cost, latency, token, and volume anomalies.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnomalySeverity(StrEnum):
    """Severity classification for anomalies (API_CONTRACT.yaml)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AnomalyStatus(StrEnum):
    """Resolution status of an anomaly record."""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _gen_uuid() -> str:
    return str(uuid.uuid4())


class ForecastRecord(Base):
    """Persisted cost forecast record matching DATABASE_SCHEMA.md section 16."""

    __tablename__ = "forecasts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scope_type: Mapped[str] = mapped_column(String, nullable=False, default="TENANT", index=True)
    scope_id: Mapped[str] = mapped_column(String, nullable=False, default="default", index=True)
    forecast_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    predicted_cost: Mapped[float] = mapped_column(Float, nullable=False)
    lower_bound: Mapped[float] = mapped_column(Float, nullable=False)
    upper_bound: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.95)
    forecast_model_name: Mapped[str] = mapped_column(
        String, nullable=False, default="baseline_linear_runrate"
    )
    forecast_model_version: Mapped[str] = mapped_column(String, nullable=False, default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AnomalyRecord(Base):
    """Persisted anomaly detection record matching DATABASE_SCHEMA.md section 17."""

    __tablename__ = "anomalies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    scope_type: Mapped[str] = mapped_column(String, nullable=False, default="TENANT", index=True)
    scope_id: Mapped[str] = mapped_column(String, nullable=False, default="default", index=True)
    anomaly_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String, nullable=False, default=AnomalySeverity.MEDIUM, index=True)
    expected_value: Mapped[float] = mapped_column(Float, nullable=False)
    actual_value: Mapped[float] = mapped_column(Float, nullable=False)
    deviation_percent: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default=AnomalyStatus.OPEN, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
