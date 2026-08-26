"""Audit ORM models.

Implements DATABASE_SCHEMA.md sections 20-21:
  audit_events, model_registry_history.

Rules (DATABASE_SCHEMA.md section 20, SECURITY.md section 16):
  - Do not store secrets in audit events.
  - Sensitive values must be redacted before storage.
  - ``approval_id`` FK is populated for any action that required an approval.

``model_registry_history`` records changes to the models table for audit
trail purposes (DATABASE_SCHEMA.md section 21).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# audit_events — DATABASE_SCHEMA.md section 20
# ---------------------------------------------------------------------------

class AuditEvent(Base):
    """Append-only audit log.

    Never store secrets in this table. Sensitive fields must be redacted at
    the service layer before an AuditEvent is created
    (SECURITY.md section 16, AI_DEVELOPMENT_RULES.md section 13).
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Denormalised — not FK; the user or tenant may be deleted while audits persist.
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)

    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # JSON-serialised state snapshots; secrets must be redacted before storage.
    before_state: Mapped[str | None] = mapped_column(String, nullable=True)
    after_state: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)

    # approval_id is SECURITY.md section 16's "approval" field.
    approval_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )

    # Network context — never store credentials here.
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"AuditEvent(id={self.id!r}, action={self.action!r}, "
            f"tenant_id={self.tenant_id!r})"
        )


# ---------------------------------------------------------------------------
# model_registry_history — DATABASE_SCHEMA.md section 21
# ---------------------------------------------------------------------------

class ModelRegistryHistory(Base):
    """Change log for model registry entries.

    ``model_id`` is stored as plain text rather than a FK so history rows
    survive model deletion (same rationale as telemetry denormalisation in
    DATABASE_SCHEMA.md section 14).
    """

    __tablename__ = "model_registry_history"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    model_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    change_type: Mapped[str | None] = mapped_column(String, nullable=True)
    old_value: Mapped[str | None] = mapped_column(String, nullable=True)
    new_value: Mapped[str | None] = mapped_column(String, nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ModelRegistryHistory(id={self.id!r}, model_id={self.model_id!r}, "
            f"change_type={self.change_type!r})"
        )
