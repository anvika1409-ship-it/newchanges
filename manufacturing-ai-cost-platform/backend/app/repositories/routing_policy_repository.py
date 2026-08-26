"""Routing policy repository.

Policies are versioned and never mutated. A change creates a new row; this
repository never updates an existing policy in place
(AI_DEVELOPMENT_RULES.md section 45, DATABASE_SCHEMA.md section 13).
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.governance import RoutingPolicy
from app.repositories.base import AsyncRepository


class RoutingPolicyRepository(AsyncRepository[RoutingPolicy]):
    """Read/write access to the ``routing_policies`` table."""

    async def get_by_id(self, policy_id: str) -> RoutingPolicy | None:
        return await self.session.get(RoutingPolicy, policy_id)

    async def get_active(
        self, tenant_id: str, workload_type: str, complexity: str
    ) -> RoutingPolicy | None:
        """Return the ACTIVE policy for the given combination, if any.

        The orchestrator calls this on every request; the query uses indexed
        columns (tenant_id, workload_type) so it stays fast.
        """
        result = await self.session.execute(
            select(RoutingPolicy).where(
                RoutingPolicy.tenant_id == tenant_id,
                RoutingPolicy.workload_type == workload_type,
                RoutingPolicy.complexity == complexity,
                RoutingPolicy.status == "ACTIVE",
            )
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: str,
        workload_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RoutingPolicy]:
        stmt = select(RoutingPolicy).where(RoutingPolicy.tenant_id == tenant_id)
        if workload_type is not None:
            stmt = stmt.where(RoutingPolicy.workload_type == workload_type)
        if status is not None:
            stmt = stmt.where(RoutingPolicy.status == status)
        result = await self.session.execute(
            stmt.order_by(RoutingPolicy.workload_type, RoutingPolicy.version.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, policy: RoutingPolicy) -> RoutingPolicy:
        """Insert a new policy version. Never mutates existing rows."""
        self.session.add(policy)
        await self.session.flush()
        return policy

    async def set_status(self, policy_id: str, status: str) -> RoutingPolicy | None:
        """Update the status column only (activation/supersede/rollback)."""
        policy = await self.session.get(RoutingPolicy, policy_id)
        if policy is None:
            return None
        policy.status = status
        await self.session.flush()
        return policy
