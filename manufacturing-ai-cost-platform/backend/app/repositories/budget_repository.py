"""Budget repository.

Budgets are tenant-scoped. The ``tenant_id`` column exists even on ENTERPRISE-
scope budgets specifically so this query can use an index without joining
parent entities (DATABASE_SCHEMA.md section 12, SECURITY.md section 5).
"""

from __future__ import annotations

from sqlalchemy import func, select, tuple_

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

    async def count_by_tenant(
        self,
        tenant_id: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> int:
        """Total matching rows, for the contract's PageInfo.total."""
        stmt = select(func.count(Budget.id)).where(Budget.tenant_id == tenant_id)
        if scope_type is not None:
            stmt = stmt.where(Budget.scope_type == scope_type)
        if scope_id is not None:
            stmt = stmt.where(Budget.scope_id == scope_id)
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_for_scopes(
        self, tenant_id: str, scopes: list[tuple[str, str]]
    ) -> list[Budget]:
        """Active budgets matching any of the given (scope_type, scope_id) pairs.

        One query rather than one per scope: budget evaluation happens before
        every AI execution (SECURITY.md section 13), so it sits on the hot path
        and must not fan out into eight round trips.

        Ordering is fixed so a caller composing several budgets into one
        decision gets a reproducible result regardless of the database's
        chosen plan.
        """
        if not scopes:
            return []
        stmt = (
            select(Budget)
            .where(
                Budget.tenant_id == tenant_id,
                Budget.status == "ACTIVE",
                tuple_(Budget.scope_type, Budget.scope_id).in_(scopes),
            )
            .order_by(Budget.scope_type, Budget.scope_id, Budget.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, budget: Budget) -> Budget:
        """Persist in-place changes to a budget row.

        Budgets are mutable, unlike routing policies: DATABASE_SCHEMA.md section
        13 requires versioning for policies and says nothing of the sort for
        budgets, and API_CONTRACT.yaml exposes PATCH /budgets/{id}. The audit
        trail for the change is the caller's responsibility (SECURITY.md
        section 16).
        """
        self.session.add(budget)
        await self.session.flush()
        return budget

    async def update_status(self, budget_id: str, status: str) -> None:
        budget = await self.session.get(Budget, budget_id)
        if budget is not None:
            budget.status = status
            await self.session.flush()
