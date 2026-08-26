"""ORM mapping for optimization_recommendations table.

Implements DATABASE_SCHEMA.md section 18 exactly:
Stores generated optimization candidates, estimated savings, impact metrics,
and proposed policy references with pending approval status.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OptimizationStatus(StrEnum):
    """Recommendation lifecycle status (API_CONTRACT.yaml)."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"


class OptimizationRiskLevel(StrEnum):
    """Risk classification for optimization actions."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _gen_uuid() -> str:
    return str(uuid.uuid4())


class OptimizationRecommendationRecord(Base):
    """Persisted optimization recommendation matching DATABASE_SCHEMA.md section 18."""

    __tablename__ = "optimization_recommendations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    workload_id: Mapped[str] = mapped_column(String, nullable=False, default="predictive_maintenance", index=True)
    current_strategy: Mapped[str] = mapped_column(String, nullable=False)
    recommended_strategy: Mapped[str] = mapped_column(String, nullable=False)
    estimated_saving: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    estimated_saving_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_impact_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_impact_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_level: Mapped[str] = mapped_column(
        String, nullable=False, default=OptimizationRiskLevel.LOW, index=True
    )
    recommendation_reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=OptimizationStatus.PENDING_APPROVAL, index=True
    )
    applied_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    superseded_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
