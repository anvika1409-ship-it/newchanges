"""Forecast endpoints.

Implements GET /forecasts matching API_CONTRACT.yaml.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.v1.schemas.analytics import Forecast, ForecastList, PageInfo
from app.repositories.forecast_repository import ForecastRepository

try:
    from app.security.dependencies import get_current_principal
    _AUTH_DEPS = [Depends(get_current_principal)]
except ImportError:
    _AUTH_DEPS = []

router = APIRouter(tags=["Forecasts"])


@router.get(
    "/forecasts",
    summary="Get AI cost forecast",
    response_model=ForecastList,
    dependencies=_AUTH_DEPS,
)
async def list_forecasts(
    request: Request,
    horizon_days: Annotated[int, Query(ge=1, le=365, description="Forecast horizon in days")] = 30,
    scope_type: Annotated[str | None, Query(description="Filter by scope type")] = None,
    scope_id: Annotated[str | None, Query(description="Filter by scope id")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ForecastList:
    """Retrieve cost forecasts matching filter criteria."""
    database = request.app.state.database

    async with database.session() as session:
        repo = ForecastRepository(session)
        items, total_count = await repo.list_forecasts(
            horizon_days=horizon_days,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
            offset=offset,
        )

        forecast_items = [
            Forecast(
                id=item.id,
                scope_type=item.scope_type,
                scope_id=item.scope_id,
                forecast_date=item.forecast_date,
                predicted_cost=item.predicted_cost,
                lower_bound=item.lower_bound,
                upper_bound=item.upper_bound,
                confidence=item.confidence,
                forecast_model_name=item.forecast_model_name,
                forecast_model_version=item.forecast_model_version,
                provenance="FORECAST",
            )
            for item in items
        ]

        return ForecastList(
            items=forecast_items,
            page=PageInfo(total=total_count, limit=limit, offset=offset),
        )
