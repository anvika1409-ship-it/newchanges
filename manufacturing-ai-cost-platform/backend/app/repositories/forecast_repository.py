"""Forecast and anomaly repositories.

All values in forecasts are FORECAST — callers must label them as such when
surfacing to users (DATABASE_SCHEMA.md section 16,
AI_DEVELOPMENT_RULES.md section 41).

Keeps SQLite queries behind SQLAlchemy abstractions (AI_DEVELOPMENT_RULES.md section 16).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.db.models.intelligence import Anomaly, Forecast
from app.repositories.base import AsyncRepository


class ForecastRepository(AsyncRepository[Forecast]):
    """Async repository for the ``forecasts`` table."""

    async def get_by_id(self, forecast_id: str) -> Forecast | None:
        return await self.session.get(Forecast, forecast_id)

    async def create(self, record: Forecast) -> Forecast:
        self.session.add(record)
        await self.session.flush()
        return record

    async def add(self, forecast: Forecast) -> Forecast:
        """Alias for create matching standard repository API."""
        return await self.create(forecast)

    async def create_many(self, records: Sequence[Forecast]) -> list[Forecast]:
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
    ) -> tuple[list[Forecast], int]:
        """Query forecasts filtered by horizon and scope, returning (items, total_count)."""
        stmt = select(Forecast)

        if tenant_id is not None:
            stmt = stmt.where(Forecast.tenant_id == tenant_id)

        if scope_type is not None:
            stmt = stmt.where(Forecast.scope_type == scope_type)

        if scope_id is not None:
            stmt = stmt.where(Forecast.scope_id == scope_id)

        if horizon_days > 0:
            max_date = date.today() + timedelta(days=horizon_days)
            stmt = stmt.where(Forecast.forecast_date <= max_date)

        # Count total matches
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = (await self.session.execute(count_stmt)).scalar_one()

        # Query page
        paged_stmt = stmt.order_by(Forecast.forecast_date.asc()).limit(limit).offset(offset)
        result = await self.session.execute(paged_stmt)
        items = list(result.scalars().all())

        return items, total_count

    async def list_by_tenant(
        self,
        tenant_id: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
        from_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Forecast]:
        """Query forecasts by tenant_id."""
        stmt = select(Forecast).where(Forecast.tenant_id == tenant_id)
        if scope_type is not None:
            stmt = stmt.where(Forecast.scope_type == scope_type)
        if scope_id is not None:
            stmt = stmt.where(Forecast.scope_id == scope_id)
        if from_date is not None:
            stmt = stmt.where(Forecast.forecast_date >= from_date)
        result = await self.session.execute(
            stmt.order_by(Forecast.forecast_date.asc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())


class AnomalyRepository(AsyncRepository[Anomaly]):
    """Async repository for the ``anomalies`` table."""

    async def get_by_id(self, anomaly_id: str) -> Anomaly | None:
        return await self.session.get(Anomaly, anomaly_id)

    async def create(self, record: Anomaly) -> Anomaly:
        self.session.add(record)
        await self.session.flush()
        return record

    async def add(self, anomaly: Anomaly) -> Anomaly:
        """Alias for create matching standard repository API."""
        return await self.create(anomaly)

    async def create_many(self, records: Sequence[Anomaly]) -> list[Anomaly]:
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
    ) -> tuple[list[Anomaly], int]:
        """Query anomalies filtered by severity, scope, and status, returning (items, total_count)."""
        stmt = select(Anomaly)

        if tenant_id is not None:
            stmt = stmt.where(Anomaly.tenant_id == tenant_id)

        if severity is not None:
            stmt = stmt.where(Anomaly.severity == severity.upper())

        if scope_type is not None:
            stmt = stmt.where(Anomaly.scope_type == scope_type)

        if scope_id is not None:
            stmt = stmt.where(Anomaly.scope_id == scope_id)

        if status is not None:
            stmt = stmt.where(Anomaly.status == status.upper())

        # Count total matches
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = (await self.session.execute(count_stmt)).scalar_one()

        # Query page
        paged_stmt = stmt.order_by(Anomaly.timestamp.desc()).limit(limit).offset(offset)
        result = await self.session.execute(paged_stmt)
        items = list(result.scalars().all())

        return items, total_count

    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Anomaly]:
        """Query anomalies by tenant_id."""
        stmt = select(Anomaly).where(Anomaly.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(Anomaly.status == status)
        if severity is not None:
            stmt = stmt.where(Anomaly.severity == severity)
        result = await self.session.execute(
            stmt.order_by(Anomaly.timestamp.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def resolve(self, anomaly_id: str, resolved_at: date | datetime | None = None) -> Anomaly | None:
        """Mark an anomaly as RESOLVED."""
        anomaly = await self.get_by_id(anomaly_id)
        if anomaly is None:
            return None
        anomaly.status = "RESOLVED"
        if resolved_at is not None:
            if isinstance(resolved_at, date) and not isinstance(resolved_at, datetime):
                resolved_at = datetime.combine(resolved_at, datetime.min.time(), tzinfo=UTC)
            anomaly.resolved_at = resolved_at
        else:
            anomaly.resolved_at = datetime.now(UTC)
        await self.session.flush()
        return anomaly
