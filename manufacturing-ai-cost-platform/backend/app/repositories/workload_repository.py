"""Workload and agent repositories.

Workloads are scoped by plant; agents by workload. Neither can be queried
without identifying the owning scope, enforcing the hierarchy established in
DATABASE_SCHEMA.md sections 2, 9, 10.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.control_plane import Agent, Workload
from app.repositories.base import AsyncRepository


class WorkloadRepository(AsyncRepository[Workload]):
    """Read/write access to the ``workloads`` table."""

    async def get_by_id(self, workload_id: str) -> Workload | None:
        return await self.session.get(Workload, workload_id)

    async def list_by_plant(
        self,
        plant_id: str,
        workload_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Workload]:
        stmt = select(Workload).where(Workload.plant_id == plant_id)
        if workload_type is not None:
            stmt = stmt.where(Workload.workload_type == workload_type)
        result = await self.session.execute(
            stmt.order_by(Workload.name).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def list_by_department(
        self, department_id: str, limit: int = 100, offset: int = 0
    ) -> list[Workload]:
        result = await self.session.execute(
            select(Workload)
            .where(Workload.department_id == department_id)
            .order_by(Workload.name)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, workload: Workload) -> Workload:
        self.session.add(workload)
        await self.session.flush()
        return workload


class AgentRepository(AsyncRepository[Agent]):
    """Read/write access to the ``agents`` table."""

    async def get_by_id(self, agent_id: str) -> Agent | None:
        return await self.session.get(Agent, agent_id)

    async def list_by_workload(
        self, workload_id: str, limit: int = 100, offset: int = 0
    ) -> list[Agent]:
        result = await self.session.execute(
            select(Agent)
            .where(Agent.workload_id == workload_id)
            .order_by(Agent.name)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, agent: Agent) -> Agent:
        self.session.add(agent)
        await self.session.flush()
        return agent
