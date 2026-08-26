"""Telemetry ORM models.

Implements DATABASE_SCHEMA.md sections 14-15:
  usage_events, cost_events.

These are append-only tables. Scope columns (tenant_id, plant_id, etc.) are
denormalised copies rather than foreign keys so telemetry rows survive
control-plane deletions and can be retained independently
(DATABASE_SCHEMA.md section 14 note, section 23).

``cost_events.provenance`` uses the values ACTUAL / ESTIMATED / UNAVAILABLE
exactly as defined in DATABASE_SCHEMA.md section 15. Do not fabricate actual
costs (AI_DEVELOPMENT_RULES.md section 10).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# usage_events — DATABASE_SCHEMA.md section 14
# ---------------------------------------------------------------------------

class UsageEvent(Base):
    """Primary telemetry record for one AI execution request.

    ``budget_decision`` values: ALLOW | DOWNGRADE | REQUIRE_APPROVAL | BLOCK.
    ``guardrail_decision`` values: ALLOW | <layer that rejected> — per-layer
    detail belongs in audit_events (DATABASE_SCHEMA.md section 14).
    """

    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint(
            "budget_decision IS NULL OR "
            "budget_decision IN ('ALLOW','DOWNGRADE','REQUIRE_APPROVAL','BLOCK')",
            name="usage_event_budget_decision_valid",
        ),
        CheckConstraint(
            "status IS NULL OR status IN ('SUCCESS','FAILURE','TIMEOUT','BLOCKED','DOWNGRADED')",
            name="usage_event_status_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Denormalised scope columns — not FKs (see module docstring).
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    plant_id: Mapped[str | None] = mapped_column(String, nullable=True)
    department_id: Mapped[str | None] = mapped_column(String, nullable=True)
    workload_id: Mapped[str | None] = mapped_column(String, nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Token usage
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Latency — execution_time_ms is total; model_latency_ms is gateway alone
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Outcome
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    retry_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fallback_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Routing context
    business_priority: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String, nullable=True)
    routing_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    guardrail_decision: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    # relationship
    cost_event: Mapped[CostEvent | None] = relationship(
        "CostEvent", back_populates="usage_event", uselist=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"UsageEvent(id={self.id!r}, request_id={self.request_id!r})"


# ---------------------------------------------------------------------------
# cost_events — DATABASE_SCHEMA.md section 15
# ---------------------------------------------------------------------------

class CostEvent(Base):
    """Cost record linked to one usage event.

    ``provenance`` must be ACTUAL, ESTIMATED, or UNAVAILABLE. Never default
    to ACTUAL when the real cost is not known
    (AI_DEVELOPMENT_RULES.md section 10).

    All amounts are in the platform base currency unless ``currency`` says
    otherwise; conversion policy lives in configuration
    (DATABASE_SCHEMA.md section 15 note).
    """

    __tablename__ = "cost_events"
    __table_args__ = (
        CheckConstraint(
            "provenance IN ('ACTUAL','ESTIMATED','UNAVAILABLE')",
            name="cost_event_provenance_valid",
        ),
        CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name="cost_event_estimated_cost_non_negative",
        ),
        CheckConstraint(
            "actual_cost IS NULL OR actual_cost >= 0",
            name="cost_event_actual_cost_non_negative",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    usage_event_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("usage_events.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Estimated vs actual — provenance distinguishes them at display time.
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="USD")
    provenance: Mapped[str] = mapped_column(String, nullable=False)

    # Cost breakdown
    input_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    infrastructure_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    # relationship
    usage_event: Mapped[UsageEvent] = relationship(
        "UsageEvent", back_populates="cost_event"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CostEvent(id={self.id!r}, provenance={self.provenance!r}, "
            f"actual_cost={self.actual_cost!r})"
        )
