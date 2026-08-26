"""Intelligence ORM models.

Implements DATABASE_SCHEMA.md sections 16-18:
  forecasts, anomalies, optimization_recommendations.

IMPORTANT labelling rules
(AI_DEVELOPMENT_RULES.md sections 41, 42, DATABASE_SCHEMA.md sections 16, 18):

  - Every value in ``forecasts`` is a FORECAST.
  - ``optimization_recommendations.estimated_saving`` and
    ``estimated_saving_percent`` are ESTIMATED or SIMULATED. They must never
    be displayed as realised savings.

``forecasts.forecast_model_name`` names the forecasting algorithm; it is
unrelated to ``models.model_name`` and must not be joined to that table
(DATABASE_SCHEMA.md section 16 note).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


import uuid

def _utcnow() -> datetime:
    return datetime.now(UTC)


def _gen_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# forecasts — DATABASE_SCHEMA.md section 16
# ---------------------------------------------------------------------------

class Forecast(Base):
    """A cost forecast for a given scope and date.

    All values are FORECAST — label them as such in any UI or API response
    (AI_DEVELOPMENT_RULES.md section 41).
    """

    __tablename__ = "forecasts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_type: Mapped[str | None] = mapped_column(String, nullable=True)
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True)
    forecast_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    #: All three cost columns are FORECAST values.
    predicted_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    lower_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The forecasting algorithm name — NOT an LLM model name.
    forecast_model_name: Mapped[str | None] = mapped_column(String, nullable=True)
    forecast_model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Forecast(id={self.id!r}, scope_type={self.scope_type!r}, "
            f"forecast_date={self.forecast_date!r})"
        )


# ---------------------------------------------------------------------------
# anomalies — DATABASE_SCHEMA.md section 17
# ---------------------------------------------------------------------------

class Anomaly(Base):
    """A detected cost or usage anomaly."""

    __tablename__ = "anomalies"
    __table_args__ = (
        CheckConstraint(
            "severity IS NULL OR severity IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="anomaly_severity_valid",
        ),
        CheckConstraint(
            "status IS NULL OR status IN ('OPEN','ACKNOWLEDGED','RESOLVED','FALSE_POSITIVE')",
            name="anomaly_status_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scope_type: Mapped[str | None] = mapped_column(String, nullable=True)
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True)
    anomaly_type: Mapped[str | None] = mapped_column(String, nullable=True)
    severity: Mapped[str | None] = mapped_column(String, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    deviation_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True, default="OPEN")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Anomaly(id={self.id!r}, anomaly_type={self.anomaly_type!r}, "
            f"severity={self.severity!r})"
        )


# ---------------------------------------------------------------------------
# optimization_recommendations — DATABASE_SCHEMA.md section 18
# ---------------------------------------------------------------------------

class OptimizationRecommendation(Base):
    """A cost/quality optimisation recommendation produced by the engine.

    ``estimated_saving`` and ``estimated_saving_percent`` are ESTIMATED values.
    They must never be displayed as realised savings
    (DATABASE_SCHEMA.md section 18, AI_DEVELOPMENT_RULES.md section 42).

    Rollback: reactivate ``superseded_policy_id``; do not delete
    ``applied_policy_id`` (AI_DEVELOPMENT_RULES.md section 45).
    """

    __tablename__ = "optimization_recommendations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','PENDING_APPROVAL','APPROVED','REJECTED','APPLIED','ROLLED_BACK')",
            name="opt_rec_status_valid",
        ),
        CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="opt_rec_risk_level_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workload_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("workloads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    current_strategy: Mapped[str | None] = mapped_column(String, nullable=True)
    recommended_strategy: Mapped[str | None] = mapped_column(String, nullable=True)
    #: ESTIMATED value — display as estimate only.
    estimated_saving: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: ESTIMATED value — display as estimate only.
    estimated_saving_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_impact_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_impact_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String, nullable=True)
    recommendation_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="DRAFT")
    #: The routing policy version this recommendation activated.
    applied_policy_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("routing_policies.id", ondelete="SET NULL"), nullable=True
    )
    #: The routing policy version it replaced (needed for rollback).
    superseded_policy_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("routing_policies.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=_utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"OptimizationRecommendation(id={self.id!r}, status={self.status!r}, "
            f"estimated_saving={self.estimated_saving!r})"
        )
