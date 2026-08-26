"""Telemetry repositories.

usage_events and cost_events are append-only. There is no update or delete
path — historical telemetry must survive control-plane changes
(DATABASE_SCHEMA.md sections 14, 23).

cost_events.provenance must be ACTUAL, ESTIMATED, or UNAVAILABLE. The
repository does not validate this; the service layer must supply the correct
value before calling add_cost_event.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.db.models.telemetry import CostEvent, UsageEvent
from app.repositories.base import AsyncRepository


class UsageEventRepository(AsyncRepository[UsageEvent]):
    """Append-only access to the ``usage_events`` table."""

    async def add(self, event: UsageEvent) -> UsageEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_by_id(self, event_id: str) -> UsageEvent | None:
        return await self.session.get(UsageEvent, event_id)

    async def get_by_request_id(self, request_id: str) -> UsageEvent | None:
        result = await self.session.execute(
            select(UsageEvent).where(UsageEvent.request_id == request_id)
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        workload_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[UsageEvent]:
        stmt = select(UsageEvent).where(UsageEvent.tenant_id == tenant_id)
        if from_ts is not None:
            stmt = stmt.where(UsageEvent.timestamp >= from_ts)
        if to_ts is not None:
            stmt = stmt.where(UsageEvent.timestamp <= to_ts)
        if workload_id is not None:
            stmt = stmt.where(UsageEvent.workload_id == workload_id)
        result = await self.session.execute(
            stmt.order_by(UsageEvent.timestamp.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())


class CostEventRepository(AsyncRepository[CostEvent]):
    """Append-only access to the ``cost_events`` table."""

    async def add(self, event: CostEvent) -> CostEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_by_usage_event(self, usage_event_id: str) -> CostEvent | None:
        result = await self.session.execute(
            select(CostEvent).where(CostEvent.usage_event_id == usage_event_id)
        )
        return result.scalar_one_or_none()
