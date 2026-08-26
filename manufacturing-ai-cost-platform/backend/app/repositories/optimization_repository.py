"""Repository for optimization recommendations persistence and querying.

Keeps SQLite queries behind SQLAlchemy abstractions (AI_DEVELOPMENT_RULES.md section 16).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select

from app.db.models.optimization import OptimizationRecommendationRecord
from app.repositories.base import AsyncRepository


class OptimizationRepository(AsyncRepository[OptimizationRecommendationRecord]):
    """Async repository for the ``optimization_recommendations`` table."""

    async def get_by_id(self, recommendation_id: str) -> OptimizationRecommendationRecord | None:
        return await self.session.get(OptimizationRecommendationRecord, recommendation_id)

    async def create(self, record: OptimizationRecommendationRecord) -> OptimizationRecommendationRecord:
        self.session.add(record)
        await self.session.flush()
        return record

    async def create_many(
        self, records: Sequence[OptimizationRecommendationRecord]
    ) -> list[OptimizationRecommendationRecord]:
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
    ) -> tuple[list[OptimizationRecommendationRecord], int]:
        """Query optimization recommendations filtered by status and workload, returning (items, total_count)."""
        stmt = select(OptimizationRecommendationRecord)

        if tenant_id is not None:
            stmt = stmt.where(OptimizationRecommendationRecord.tenant_id == tenant_id)

        if status is not None:
            stmt = stmt.where(OptimizationRecommendationRecord.status == status.upper())

        if workload_id is not None:
            stmt = stmt.where(OptimizationRecommendationRecord.workload_id == workload_id)

        # Count total matches
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = (await self.session.execute(count_stmt)).scalar_one()

        # Query page
        paged_stmt = (
            stmt.order_by(OptimizationRecommendationRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(paged_stmt)
        items = list(result.scalars().all())

        return items, total_count
