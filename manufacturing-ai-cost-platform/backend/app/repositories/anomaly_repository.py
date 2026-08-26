"""Repository for anomaly persistence and querying.

Keeps SQLite queries behind SQLAlchemy abstractions (AI_DEVELOPMENT_RULES.md section 16).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select

from app.db.models.analytics import AnomalyRecord
from app.repositories.base import AsyncRepository


class AnomalyRepository(AsyncRepository[AnomalyRecord]):
    """Async repository for the ``anomalies`` table."""

    async def get_by_id(self, anomaly_id: str) -> AnomalyRecord | None:
        return await self.session.get(AnomalyRecord, anomaly_id)

    async def create(self, record: AnomalyRecord) -> AnomalyRecord:
        self.session.add(record)
        await self.session.flush()
        return record

    async def create_many(self, records: Sequence[AnomalyRecord]) -> list[AnomalyRecord]:
        self.session.add_all(records)
        await self.session.flush()
        return list(records)

    async def list_anomalies(
        self,
        *,
        severity: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AnomalyRecord], int]:
        """Query anomalies filtered by severity, scope, and status, returning (items, total_count)."""
        stmt = select(AnomalyRecord)

        if tenant_id is not None:
            stmt = stmt.where(AnomalyRecord.tenant_id == tenant_id)

        if severity is not None:
            stmt = stmt.where(AnomalyRecord.severity == severity.upper())

        if scope_type is not None:
            stmt = stmt.where(AnomalyRecord.scope_type == scope_type)

        if scope_id is not None:
            stmt = stmt.where(AnomalyRecord.scope_id == scope_id)

        if status is not None:
            stmt = stmt.where(AnomalyRecord.status == status.upper())

        # Count total matches
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = (await self.session.execute(count_stmt)).scalar_one()

        # Query page
        paged_stmt = stmt.order_by(AnomalyRecord.timestamp.desc()).limit(limit).offset(offset)
        result = await self.session.execute(paged_stmt)
        items = list(result.scalars().all())

        return items, total_count
