"""Telemetry repositories.

usage_events and cost_events are append-only. There is no update or delete
path — historical telemetry must survive control-plane changes
(DATABASE_SCHEMA.md sections 14, 23).

cost_events.provenance must be ACTUAL, ESTIMATED, or UNAVAILABLE. The
repository does not validate this; the service layer must supply the correct
value before calling add_cost_event.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models.telemetry import CostEvent, UsageEvent
from app.repositories.base import AsyncRepository


def _gen_uuid() -> str:
    return str(uuid.uuid4())


class UsageEventRepository(AsyncRepository[UsageEvent]):
    """Append-only access to the ``usage_events`` table."""

    async def add(self, event: UsageEvent) -> UsageEvent:
        if not event.id:
            event.id = _gen_uuid()
        self.session.add(event)
        await self.session.flush()
        return event

    async def create(self, event: UsageEvent) -> UsageEvent:
        """Alias for add."""
        return await self.add(event)

    async def create_many(self, events: Sequence[UsageEvent]) -> list[UsageEvent]:
        for event in events:
            if not event.id:
                event.id = _gen_uuid()
        self.session.add_all(events)
        await self.session.flush()
        return list(events)

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

    async def query_telemetry(
        self,
        tenant_id: str,
        *,
        plant_id: str | None = None,
        department_id: str | None = None,
        workload_id: str | None = None,
        agent_id: str | None = None,
        model_id: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[UsageEvent]:
        """Query usage events with their linked cost events, enforcing tenant isolation."""
        stmt = (
            select(UsageEvent)
            .options(selectinload(UsageEvent.cost_event))
            .where(UsageEvent.tenant_id == tenant_id)
        )
        if plant_id is not None:
            stmt = stmt.where(UsageEvent.plant_id == plant_id)
        if department_id is not None:
            stmt = stmt.where(UsageEvent.department_id == department_id)
        if workload_id is not None:
            stmt = stmt.where(UsageEvent.workload_id == workload_id)
        if agent_id is not None:
            stmt = stmt.where(UsageEvent.agent_id == agent_id)
        if model_id is not None:
            stmt = stmt.where(UsageEvent.model_id == model_id)
        if from_ts is not None:
            stmt = stmt.where(UsageEvent.timestamp >= from_ts)
        if to_ts is not None:
            stmt = stmt.where(UsageEvent.timestamp <= to_ts)

        stmt = stmt.order_by(UsageEvent.timestamp.asc())
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class CostEventRepository(AsyncRepository[CostEvent]):
    """Append-only access to the ``cost_events`` table."""

    async def add(self, event: CostEvent) -> CostEvent:
        if not event.id:
            event.id = _gen_uuid()
        self.session.add(event)
        await self.session.flush()
        return event

    async def create(self, event: CostEvent) -> CostEvent:
        """Alias for add."""
        return await self.add(event)

    async def create_many(self, events: Sequence[CostEvent]) -> list[CostEvent]:
        for event in events:
            if not event.id:
                event.id = _gen_uuid()
        self.session.add_all(events)
        await self.session.flush()
        return list(events)

    async def get_by_usage_event(self, usage_event_id: str) -> CostEvent | None:
        result = await self.session.execute(
            select(CostEvent).where(CostEvent.usage_event_id == usage_event_id)
        )
        return result.scalar_one_or_none()
