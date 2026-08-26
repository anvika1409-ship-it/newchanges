"""Audit repositories.

audit_events and model_registry_history are append-only. There is no update
or delete path — audit integrity requires immutability
(SECURITY.md section 16, DATABASE_SCHEMA.md section 20).

Secrets must be redacted BEFORE calling add_audit_event. The repository
does not redact; that is the service layer's responsibility
(AI_DEVELOPMENT_RULES.md section 13).
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.audit import AuditEvent, ModelRegistryHistory
from app.repositories.base import AsyncRepository


class AuditEventRepository(AsyncRepository[AuditEvent]):
    """Append-only access to the ``audit_events`` table."""

    async def add(self, event: AuditEvent) -> AuditEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_by_tenant(
        self,
        tenant_id: str,
        action: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[AuditEvent]:
        stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
        if action is not None:
            stmt = stmt.where(AuditEvent.action == action)
        result = await self.session.execute(
            stmt.order_by(AuditEvent.timestamp.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())


class ModelRegistryHistoryRepository(AsyncRepository[ModelRegistryHistory]):
    """Append-only access to the ``model_registry_history`` table."""

    async def add(self, entry: ModelRegistryHistory) -> ModelRegistryHistory:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_by_model(
        self, model_id: str, limit: int = 100, offset: int = 0
    ) -> list[ModelRegistryHistory]:
        result = await self.session.execute(
            select(ModelRegistryHistory)
            .where(ModelRegistryHistory.model_id == model_id)
            .order_by(ModelRegistryHistory.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())
