"""ORM mapping for routing_policies table.

Implements DATABASE_SCHEMA.md section 13:
Enforces immutable versioning, policy statuses (DRAFT, PENDING_APPROVAL, CANARY,
ACTIVE, SUPERSEDED, ROLLED_BACK), and request-level limits.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PolicyStatus(StrEnum):
    """Routing policy lifecycle status (DATABASE_SCHEMA.md section 13)."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    CANARY = "CANARY"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ROLLED_BACK = "ROLLED_BACK"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _gen_uuid() -> str:
    return str(uuid.uuid4())


class RoutingPolicyRecord(Base):
    """Persisted routing policy record with immutable versioning."""

    __tablename__ = "routing_policies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    workload_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    complexity: Mapped[str] = mapped_column(String, nullable=False, default="STANDARD")
    business_priority: Mapped[str | None] = mapped_column(String, nullable=True, default="NORMAL")
    selected_model_id: Mapped[str | None] = mapped_column(String, nullable=True)
    selected_agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    max_context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tool_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_cost_per_request: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_total_tokens_per_request: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minimum_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String, nullable=True, default="LOW")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=PolicyStatus.DRAFT, index=True
    )
    canary_traffic_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
