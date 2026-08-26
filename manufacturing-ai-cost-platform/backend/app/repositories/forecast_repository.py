"""Forecast and anomaly repositories.

All values in forecasts are FORECAST — callers must label them as such when
surfacing to users (DATABASE_SCHEMA.md section 16,
AI_DEVELOPMENT_RULES.md section 41).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.db.models.intelligence import Anomaly, Forecast
from app.repositories.base import AsyncRepository


class ForecastRepository(AsyncRepository[Forecast]):
    """Read/write access to the ``forecasts`` table."""

    async def get_by_id(self, forecast_id: str) -> Forecast | None:
        return await self.session.get(Forecast, forecast_id)

    async def list_by_tenant(
        self,
        tenant_id: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
        from_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Forecast]:
        stmt = select(Forecast).where(Forecast.tenant_id == tenant_id)
        if scope_type is not None:
            stmt = stmt.where(Forecast.scope_type == scope_type)
        if scope_id is not None:
            stmt = stmt.where(Forecast.scope_id == scope_id)
        if from_date is not None:
            stmt = stmt.where(Forecast.forecast_date >= from_date)
        result = await self.session.execute(
            stmt.order_by(Forecast.forecast_date).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, forecast: Forecast) -> Forecast:
        self.session.add(forecast)
        await self.session.flush()
        return forecast


class AnomalyRepository(AsyncRepository[Anomaly]):
    """Read/write access to the ``anomalies`` table."""

    async def get_by_id(self, anomaly_id: str) -> Anomaly | None:
        return await self.session.get(Anomaly, anomaly_id)

    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Anomaly]:
        stmt = select(Anomaly).where(Anomaly.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(Anomaly.status == status)
        if severity is not None:
            stmt = stmt.where(Anomaly.severity == severity)
        result = await self.session.execute(
            stmt.order_by(Anomaly.timestamp.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, anomaly: Anomaly) -> Anomaly:
        self.session.add(anomaly)
        await self.session.flush()
        return anomaly

    async def resolve(self, anomaly_id: str, resolved_at: date) -> Anomaly | None:
        anomaly = await self.session.get(Anomaly, anomaly_id)
        if anomaly is None:
            return None
        anomaly.status = "RESOLVED"
        anomaly.resolved_at = resolved_at  # type: ignore[assignment]
        await self.session.flush()
        return anomaly
