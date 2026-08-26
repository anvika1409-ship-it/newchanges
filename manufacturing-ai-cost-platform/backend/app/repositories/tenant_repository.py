"""Tenant repository.

Keeps all tenants table access behind SQLAlchemy. Business services call this
repository; they never write SQL directly
(AI_DEVELOPMENT_RULES.md section 16, DATABASE_SCHEMA.md section 1).
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.control_plane import Tenant
from app.repositories.base import AsyncRepository


class TenantRepository(AsyncRepository[Tenant]):
    """Read/write access to the ``tenants`` table."""

    async def get_by_id(self, tenant_id: str) -> Tenant | None:
        return await self.session.get(Tenant, tenant_id)

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[Tenant]:
        result = await self.session.execute(
            select(Tenant).order_by(Tenant.name).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, tenant: Tenant) -> Tenant:
        self.session.add(tenant)
        await self.session.flush()
        return tenant

    async def exists(self, tenant_id: str) -> bool:
        return await self.session.get(Tenant, tenant_id) is not None
