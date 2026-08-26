"""Plant and department repositories.

All list queries are tenant-scoped so a plant manager cannot read another
tenant's plants (SECURITY.md section 5).
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.control_plane import Department, Plant
from app.repositories.base import AsyncRepository


class PlantRepository(AsyncRepository[Plant]):
    """Read/write access to the ``plants`` table."""

    async def get_by_id(self, plant_id: str, tenant_id: str) -> Plant | None:
        result = await self.session.execute(
            select(Plant).where(Plant.id == plant_id, Plant.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self, tenant_id: str, limit: int = 100, offset: int = 0
    ) -> list[Plant]:
        result = await self.session.execute(
            select(Plant)
            .where(Plant.tenant_id == tenant_id)
            .order_by(Plant.name)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, plant: Plant) -> Plant:
        self.session.add(plant)
        await self.session.flush()
        return plant


class DepartmentRepository(AsyncRepository[Department]):
    """Read/write access to the ``departments`` table."""

    async def get_by_id(self, department_id: str) -> Department | None:
        return await self.session.get(Department, department_id)

    async def list_by_plant(
        self, plant_id: str, limit: int = 100, offset: int = 0
    ) -> list[Department]:
        result = await self.session.execute(
            select(Department)
            .where(Department.plant_id == plant_id)
            .order_by(Department.name)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, department: Department) -> Department:
        self.session.add(department)
        await self.session.flush()
        return department
