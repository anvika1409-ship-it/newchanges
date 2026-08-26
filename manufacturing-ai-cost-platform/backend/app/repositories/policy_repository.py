"""Repository for routing policies persistence and querying.

Keeps SQLite queries behind SQLAlchemy abstractions (AI_DEVELOPMENT_RULES.md section 16).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select

from app.db.models.policy import PolicyStatus, RoutingPolicyRecord
from app.repositories.base import AsyncRepository


class PolicyRepository(AsyncRepository[RoutingPolicyRecord]):
    """Async repository for the ``routing_policies`` table."""

    async def get_by_id(self, policy_id: str) -> RoutingPolicyRecord | None:
        return await self.session.get(RoutingPolicyRecord, policy_id)

    async def create(self, record: RoutingPolicyRecord) -> RoutingPolicyRecord:
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_active_policy(
        self, workload_type: str, tenant_id: str | None = None
    ) -> RoutingPolicyRecord | None:
        """Get the currently active policy for a workload."""
        stmt = (
            select(RoutingPolicyRecord)
            .where(
                RoutingPolicyRecord.workload_type == workload_type,
                RoutingPolicyRecord.status.in_([PolicyStatus.ACTIVE, PolicyStatus.CANARY]),
            )
            .order_by(RoutingPolicyRecord.version.desc())
            .limit(1)
        )
        if tenant_id is not None:
            stmt = stmt.where(RoutingPolicyRecord.tenant_id == tenant_id)

        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_latest_version_number(
        self, workload_type: str, tenant_id: str | None = None
    ) -> int:
        """Get the highest version number for a workload."""
        stmt = select(func.max(RoutingPolicyRecord.version)).where(
            RoutingPolicyRecord.workload_type == workload_type
        )
        if tenant_id is not None:
            stmt = stmt.where(RoutingPolicyRecord.tenant_id == tenant_id)

        result = await self.session.execute(stmt)
        max_ver = result.scalar()
        return max_ver if max_ver is not None else 0

    async def list_policies(
        self,
        *,
        workload_type: str | None = None,
        status: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RoutingPolicyRecord], int]:
        stmt = select(RoutingPolicyRecord)

        if tenant_id is not None:
            stmt = stmt.where(RoutingPolicyRecord.tenant_id == tenant_id)
        if workload_type is not None:
            stmt = stmt.where(RoutingPolicyRecord.workload_type == workload_type)
        if status is not None:
            stmt = stmt.where(RoutingPolicyRecord.status == status.upper())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = (await self.session.execute(count_stmt)).scalar_one()

        paged_stmt = stmt.order_by(RoutingPolicyRecord.version.desc()).limit(limit).offset(offset)
        result = await self.session.execute(paged_stmt)
        items = list(result.scalars().all())

        return items, total_count
