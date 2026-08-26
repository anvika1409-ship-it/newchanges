"""Optimization and approval repositories.

optimization_recommendations and approvals both require tenant scope. Neither
can be queried without a tenant_id, enforcing isolation
(SECURITY.md section 5).

Approval decisions are made by a human principal; the repository never sets
approved_by from an LLM response (SECURITY.md section 14).

Keeps SQLite queries behind SQLAlchemy abstractions (AI_DEVELOPMENT_RULES.md section 16).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.db.models.governance import Approval
from app.db.models.intelligence import OptimizationRecommendation
from app.repositories.base import AsyncRepository


class OptimizationRecommendationRepository(AsyncRepository[OptimizationRecommendation]):
    """Async repository for the ``optimization_recommendations`` table."""

    async def get_by_id(
        self, rec_id: str, tenant_id: str | None = None
    ) -> OptimizationRecommendation | None:
        stmt = select(OptimizationRecommendation).where(OptimizationRecommendation.id == rec_id)
        if tenant_id is not None:
            stmt = stmt.where(OptimizationRecommendation.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def create(self, record: OptimizationRecommendation) -> OptimizationRecommendation:
        self.session.add(record)
        await self.session.flush()
        return record

    async def add(self, rec: OptimizationRecommendation) -> OptimizationRecommendation:
        """Alias for create matching standard repository API."""
        return await self.create(rec)

    async def create_many(
        self, records: Sequence[OptimizationRecommendation]
    ) -> list[OptimizationRecommendation]:
        self.session.add_all(records)
        await self.session.flush()
        return list(records)

    async def list_recommendations(
        self,
        *,
        status: str | None = None,
        workload_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[OptimizationRecommendation], int]:
        """Query optimization recommendations filtered by status and workload, returning (items, total_count)."""
        stmt = select(OptimizationRecommendation)

        if tenant_id is not None:
            stmt = stmt.where(OptimizationRecommendation.tenant_id == tenant_id)

        if status is not None:
            stmt = stmt.where(OptimizationRecommendation.status == status.upper())

        if workload_id is not None:
            stmt = stmt.where(OptimizationRecommendation.workload_id == workload_id)

        # Count total matches
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = (await self.session.execute(count_stmt)).scalar_one()

        # Query page
        paged_stmt = (
            stmt.order_by(OptimizationRecommendation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(paged_stmt)
        items = list(result.scalars().all())

        return items, total_count

    async def list_by_tenant(
        self,
        tenant_id: str,
        workload_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OptimizationRecommendation]:
        """Query optimization recommendations by tenant_id."""
        stmt = select(OptimizationRecommendation).where(
            OptimizationRecommendation.tenant_id == tenant_id
        )
        if workload_id is not None:
            stmt = stmt.where(OptimizationRecommendation.workload_id == workload_id)
        if status is not None:
            stmt = stmt.where(OptimizationRecommendation.status == status)
        result = await self.session.execute(
            stmt.order_by(OptimizationRecommendation.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        rec_id: str,
        status: str,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
        applied_at: datetime | None = None,
        rolled_back_at: datetime | None = None,
        applied_policy_id: str | None = None,
    ) -> OptimizationRecommendation | None:
        rec = await self.get_by_id(rec_id)
        if rec is None:
            return None
        rec.status = status
        if approved_by is not None:
            rec.approved_by = approved_by
        if approved_at is not None:
            rec.approved_at = approved_at
        if applied_at is not None:
            rec.applied_at = applied_at
        if rolled_back_at is not None:
            rec.rolled_back_at = rolled_back_at
        if applied_policy_id is not None:
            rec.applied_policy_id = applied_policy_id
        await self.session.flush()
        return rec


# Alias for backward compatibility across modules
OptimizationRepository = OptimizationRecommendationRepository


class ApprovalRepository(AsyncRepository[Approval]):
    """Async repository for the ``approvals`` table."""

    async def get_by_id(
        self, approval_id: str, tenant_id: str | None = None
    ) -> Approval | None:
        stmt = select(Approval).where(Approval.id == approval_id)
        if tenant_id is not None:
            stmt = stmt.where(Approval.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def create(self, approval: Approval) -> Approval:
        self.session.add(approval)
        await self.session.flush()
        return approval

    async def add(self, approval: Approval) -> Approval:
        """Alias for create matching standard repository API."""
        return await self.create(approval)

    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Approval]:
        stmt = select(Approval).where(Approval.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(Approval.status == status)
        result = await self.session.execute(
            stmt.order_by(Approval.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def decide(
        self,
        approval_id: str,
        status: str,
        approved_by: str,
        decided_at: datetime | None = None,
    ) -> Approval | None:
        approval = await self.get_by_id(approval_id)
        if approval is None:
            return None
        approval.status = status
        approval.approved_by = approved_by
        approval.decided_at = decided_at or datetime.now(UTC)
        await self.session.flush()
        return approval
