"""Governance ORM models.

Implements DATABASE_SCHEMA.md sections 12-13 and 19:
  budgets, routing_policies, approvals.

Routing policies are versioned and never overwritten destructively
(AI_DEVELOPMENT_RULES.md section 45, DATABASE_SCHEMA.md section 13).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
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
# Shared enumerations
# ---------------------------------------------------------------------------

class BudgetScope(str):
    """DATABASE_SCHEMA.md section 12 — budget scope values."""
    ENTERPRISE = "ENTERPRISE"
    TENANT = "TENANT"
    PLANT = "PLANT"
    DEPARTMENT = "DEPARTMENT"
    WORKLOAD = "WORKLOAD"
    AGENT = "AGENT"
    MODEL = "MODEL"


class BudgetPeriod(str):
    DAILY = "DAILY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"


class RoutingPolicyStatus(str):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    CANARY = "CANARY"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ROLLED_BACK = "ROLLED_BACK"


class ApprovalStatus(str):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# ---------------------------------------------------------------------------
# budgets — DATABASE_SCHEMA.md section 12
# ---------------------------------------------------------------------------

class Budget(Base):
    """A spend limit at a given scope.

    ``tenant_id`` is always required even for ENTERPRISE scope so that
    tenant-isolation queries (SECURITY.md section 5) can use an indexed
    column rather than a join through the parent hierarchy.

    Request-level limits are NOT budgets; they live on routing_policies
    (DATABASE_SCHEMA.md section 12 note).
    """

    __tablename__ = "budgets"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('ENTERPRISE','TENANT','PLANT','DEPARTMENT','WORKLOAD','AGENT','MODEL')",
            name="budget_scope_type_valid",
        ),
        CheckConstraint(
            "period IN ('DAILY','MONTHLY','QUARTERLY','ANNUAL')",
            name="budget_period_valid",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','INACTIVE','EXCEEDED')",
            name="budget_status_valid",
        ),
        CheckConstraint("amount > 0", name="budget_amount_positive"),
        CheckConstraint(
            "warning_threshold_percent > 0 AND warning_threshold_percent <= 100",
            name="budget_warning_threshold_valid",
        ),
        CheckConstraint(
            "critical_threshold_percent > 0 AND critical_threshold_percent <= 100",
            name="budget_critical_threshold_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    scope_type: Mapped[str] = mapped_column(String, nullable=False)
    scope_id: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="USD")
    period: Mapped[str] = mapped_column(String, nullable=False, default=BudgetPeriod.MONTHLY)
    warning_threshold_percent: Mapped[float] = mapped_column(Float, nullable=False, default=80.0)
    critical_threshold_percent: Mapped[float] = mapped_column(Float, nullable=False, default=95.0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ACTIVE")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Budget(id={self.id!r}, scope_type={self.scope_type!r}, "
            f"scope_id={self.scope_id!r}, amount={self.amount!r})"
        )


# ---------------------------------------------------------------------------
# routing_policies — DATABASE_SCHEMA.md section 13
# ---------------------------------------------------------------------------

class RoutingPolicy(Base):
    """A versioned routing policy.

    Policies are never updated destructively — a change creates a new row
    with an incremented ``version`` (AI_DEVELOPMENT_RULES.md section 45).

    ``canary_traffic_percent`` is only meaningful when ``status = CANARY``
    (DATABASE_SCHEMA.md section 13, SECURITY.md section 15).

    ``max_total_tokens_per_request`` is the per-request token cap
    (DATABASE_SCHEMA.md section 13 note).
    """

    __tablename__ = "routing_policies"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','PENDING_APPROVAL','CANARY','ACTIVE','SUPERSEDED','ROLLED_BACK')",
            name="routing_policy_status_valid",
        ),
        CheckConstraint(
            "complexity IN ('simple','medium','complex')",
            name="routing_policy_complexity_valid",
        ),
        CheckConstraint(
            "canary_traffic_percent IS NULL OR "
            "(canary_traffic_percent >= 0 AND canary_traffic_percent <= 100)",
            name="routing_policy_canary_traffic_valid",
        ),
        CheckConstraint("version >= 1", name="routing_policy_version_positive"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workload_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    complexity: Mapped[str] = mapped_column(String, nullable=False)
    business_priority: Mapped[str | None] = mapped_column(String, nullable=True)
    selected_model_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("models.id", ondelete="SET NULL"), nullable=True
    )
    selected_agent_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    max_context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tool_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_cost_per_request: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Per-request output token cap (SECURITY.md section 13).
    max_total_tokens_per_request: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minimum_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=RoutingPolicyStatus.DRAFT
    )
    canary_traffic_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=_utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"RoutingPolicy(id={self.id!r}, workload_type={self.workload_type!r}, "
            f"version={self.version!r}, status={self.status!r})"
        )


# ---------------------------------------------------------------------------
# approvals — DATABASE_SCHEMA.md section 19
# ---------------------------------------------------------------------------

class Approval(Base):
    """Human-in-the-loop approval record.

    Backs every approval path: REQUIRE_APPROVAL budget outcomes, high-risk
    manufacturing actions, high-risk tool calls, and optimization activations
    (DATABASE_SCHEMA.md section 19, SECURITY.md sections 11, 14).

    A model result can never satisfy its own approval (SECURITY.md section 14).
    """

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','EXPIRED')",
            name="approval_status_valid",
        ),
        CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="approval_risk_level_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    risk_level: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=ApprovalStatus.PENDING
    )
    comments: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Approval(id={self.id!r}, resource_type={self.resource_type!r}, "
            f"status={self.status!r})"
        )
