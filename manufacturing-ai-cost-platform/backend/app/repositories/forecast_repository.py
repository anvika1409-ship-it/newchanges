"""Repository for forecast persistence and querying.

Keeps SQLite queries behind SQLAlchemy abstractions (AI_DEVELOPMENT_RULES.md section 16).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from app.db.models.analytics import ForecastRecord
from app.repositories.base import AsyncRepository


class ForecastRepository(AsyncRepository[ForecastRecord]):
    """Async repository for the ``forecasts`` table."""

    async def get_by_id(self, forecast_id: str) -> ForecastRecord | None:
        return await self.session.get(ForecastRecord, forecast_id)

    async def create(self, record: ForecastRecord) -> ForecastRecord:
        self.session.add(record)
        await self.session.flush()
        return record

    async def create_many(self, records: Sequence[ForecastRecord]) -> list[ForecastRecord]:
        self.session.add_all(records)
        await self.session.flush()
        return list(records)

    async def list_forecasts(
        self,
        *,
        horizon_days: int = 30,
        scope_type: str | None = None,
        scope_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ForecastRecord], int]:
        """Query forecasts filtered by horizon and scope, returning (items, total_count)."""
        stmt = select(ForecastRecord)

        if tenant_id is not None:
            stmt = stmt.where(ForecastRecord.tenant_id == tenant_id)

        if scope_type is not None:
            stmt = stmt.where(ForecastRecord.scope_type == scope_type)

        if scope_id is not None:
            stmt = stmt.where(ForecastRecord.scope_id == scope_id)

        if horizon_days > 0:
            max_date = date.today() + timedelta(days=horizon_days)
            stmt = stmt.where(ForecastRecord.forecast_date <= max_date)

        # Count total matches
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = (await self.session.execute(count_stmt)).scalar_one()

        # Query page
        paged_stmt = stmt.order_by(ForecastRecord.forecast_date.asc()).limit(limit).offset(offset)
        result = await self.session.execute(paged_stmt)
        items = list(result.scalars().all())

        return items, total_count
