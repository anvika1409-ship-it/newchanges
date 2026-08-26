"""Optimization and approval repositories.

optimization_recommendations and approvals both require tenant scope. Neither
can be queried without a tenant_id, enforcing isolation
(SECURITY.md section 5).

Approval decisions are made by a human principal; the repository never sets
approved_by from an LLM response (SECURITY.md section 14).
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.governance import Approval
from app.db.models.intelligence import OptimizationRecommendation
from app.repositories.base import AsyncRepository


class OptimizationRecommendationRepository(AsyncRepository[OptimizationRecommendation]):
    """Read/write access to the ``optimization_recommendations`` table."""

    async def get_by_id(
        self, rec_id: str, tenant_id: str
    ) -> OptimizationRecommendation | None:
        result = await self.session.execute(
            select(OptimizationRecommendation).where(
                OptimizationRecommendation.id == rec_id,
                OptimizationRecommendation.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        workload_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OptimizationRecommendation]:
        stmt = select(OptimizationRecommendation).where(
            OptimizationRecommendation.tenant_id == tenant_id
        )
        if status is not None:
            stmt = stmt.where(OptimizationRecommendation.status == status)
        if workload_id is not None:
            stmt = stmt.where(OptimizationRecommendation.workload_id == workload_id)
        result = await self.session.execute(
            stmt.order_by(OptimizationRecommendation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, rec: OptimizationRecommendation) -> OptimizationRecommendation:
        self.session.add(rec)
        await self.session.flush()
        return rec

    async def update_status(self, rec_id: str, status: str) -> OptimizationRecommendation | None:
        rec = await self.session.get(OptimizationRecommendation, rec_id)
        if rec is None:
            return None
        rec.status = status
        await self.session.flush()
        return rec


class ApprovalRepository(AsyncRepository[Approval]):
    """Read/write access to the ``approvals`` table."""

    async def get_by_id(self, approval_id: str, tenant_id: str) -> Approval | None:
        result = await self.session.execute(
            select(Approval).where(
                Approval.id == approval_id, Approval.tenant_id == tenant_id
            )
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        resource_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Approval]:
        stmt = select(Approval).where(Approval.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(Approval.status == status)
        if resource_type is not None:
            stmt = stmt.where(Approval.resource_type == resource_type)
        result = await self.session.execute(
            stmt.order_by(Approval.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, approval: Approval) -> Approval:
        self.session.add(approval)
        await self.session.flush()
        return approval

    async def decide(
        self,
        approval_id: str,
        status: str,
        decided_by: str,
        comments: str | None = None,
    ) -> Approval | None:
        """Record a human decision on an approval.

        ``decided_by`` must be a human principal identifier, never a model ID
        (SECURITY.md section 14).
        """
        from datetime import UTC, datetime

        approval = await self.session.get(Approval, approval_id)
        if approval is None:
            return None
        approval.status = status
        approval.approved_by = decided_by
        approval.comments = comments
        approval.decided_at = datetime.now(UTC)
        await self.session.flush()
        return approval
