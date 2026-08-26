"""Tool registry ORM model.

Implements DATABASE_SCHEMA.md section 11.1.

Every tool that the platform's agents may call must be registered here. A
model cannot call an unregistered tool (SECURITY.md section 11). The registry
is database-backed so it can be updated at runtime without a code deploy.

``allowed_roles`` and ``allowed_workloads`` are stored as serialized JSON
lists (TEXT column). The guardrail layer is responsible for deserialising and
enforcing them; no business logic belongs here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Tool(Base):
    """One registered tool.

    ``allowed_roles`` stores a JSON-serialised list of role names, e.g.
    ``'["AI_ENGINEER","ADMIN"]'``. ``allowed_workloads`` stores a
    JSON-serialised list of workload types (or ``null`` for unrestricted).

    High-risk tools require an approval record before execution
    (DATABASE_SCHEMA.md section 11.1, SECURITY.md section 11).
    """

    __tablename__ = "tools"
    __table_args__ = (
        CheckConstraint(
            "risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="tool_risk_level_valid",
        ),
        CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name="tool_estimated_cost_non_negative",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    #: JSON list of role names, e.g. '["AI_ENGINEER","ADMIN"]'
    allowed_roles: Mapped[str] = mapped_column(String, nullable=False)
    #: JSON list of workload types, or null for unrestricted
    allowed_workloads: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_level: Mapped[str] = mapped_column(String, nullable=False, default="LOW")
    #: Estimated cost per call in the platform base currency; null = unknown
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"Tool(id={self.id!r}, name={self.name!r}, enabled={self.enabled!r})"
