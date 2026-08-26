"""Budget repository.

Budgets are tenant-scoped. The ``tenant_id`` column exists even on ENTERPRISE-
scope budgets specifically so this query can use an index without joining
parent entities (DATABASE_SCHEMA.md section 12, SECURITY.md section 5).
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.governance import Budget
from app.repositories.base import AsyncRepository


class BudgetRepository(AsyncRepository[Budget]):
    """Read/write access to the ``budgets`` table."""

    async def get_by_id(self, budget_id: str, tenant_id: str) -> Budget | None:
        result = await self.session.execute(
            select(Budget).where(Budget.id == budget_id, Budget.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Budget]:
        stmt = select(Budget).where(Budget.tenant_id == tenant_id)
        if scope_type is not None:
            stmt = stmt.where(Budget.scope_type == scope_type)
        if scope_id is not None:
            stmt = stmt.where(Budget.scope_id == scope_id)
        result = await self.session.execute(
            stmt.order_by(Budget.scope_type, Budget.scope_id).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def get_for_scope(
        self, tenant_id: str, scope_type: str, scope_id: str
    ) -> Budget | None:
        """Return the active budget for a specific scope, if any."""
        result = await self.session.execute(
            select(Budget).where(
                Budget.tenant_id == tenant_id,
                Budget.scope_type == scope_type,
                Budget.scope_id == scope_id,
                Budget.status == "ACTIVE",
            )
        )
        return result.scalar_one_or_none()

    async def add(self, budget: Budget) -> Budget:
        self.session.add(budget)
        await self.session.flush()
        return budget

    async def update_status(self, budget_id: str, status: str) -> None:
        budget = await self.session.get(Budget, budget_id)
        if budget is not None:
            budget.status = status
            await self.session.flush()
