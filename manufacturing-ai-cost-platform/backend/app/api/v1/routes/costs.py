"""Cost and telemetry analytics endpoints.

Implements GET /cost/summary, /cost/by-model, /cost/by-agent, /cost/by-plant, /cost/trend
matching API_CONTRACT.yaml definitions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.v1.schemas.costs import CostBreakdown, CostSummary, CostTrend
from app.repositories.budget_repository import BudgetRepository
from app.repositories.telemetry_repository import CostEventRepository, UsageEventRepository
from app.services.cost_aggregation import CostAggregationService

try:
    from app.security.dependencies import get_current_principal
    _AUTH_DEPS = [Depends(get_current_principal)]
except ImportError:
    _AUTH_DEPS = []

router = APIRouter(prefix="/cost", tags=["Costs"])


@router.get(
    "/summary",
    summary="Get current cost summary",
    response_model=CostSummary,
    dependencies=_AUTH_DEPS,
)
async def get_cost_summary(
    request: Request,
    plant_id: Annotated[str | None, Query(alias="plant_id", description="Filter by plant")] = None,
    department_id: Annotated[str | None, Query(alias="department_id", description="Filter by department")] = None,
    from_: Annotated[datetime | None, Query(alias="from", description="Start timestamp")] = None,
    to: Annotated[datetime | None, Query(alias="to", description="End timestamp")] = None,
) -> CostSummary:
    """Retrieve cost summary with actual vs. estimated cost breakdown."""
    database = request.app.state.database
    principal = getattr(request.state, "principal", None)
    tenant_id = getattr(principal, "tenant_id", "tenant-1") or "tenant-1"

    async with database.session() as session:
        usage_repo = UsageEventRepository(session)
        cost_repo = CostEventRepository(session)
        budget_repo = BudgetRepository(session)
        service = CostAggregationService(usage_repo, cost_repo, budget_repo)

        return await service.get_cost_summary(
            tenant_id,
            plant_id=plant_id,
            department_id=department_id,
            from_ts=from_,
            to_ts=to,
        )


@router.get(
    "/by-model",
    summary="Get cost grouped by model",
    response_model=CostBreakdown,
    dependencies=_AUTH_DEPS,
)
async def get_cost_by_model(
    request: Request,
    plant_id: Annotated[str | None, Query(alias="plant_id", description="Filter by plant")] = None,
    department_id: Annotated[str | None, Query(alias="department_id", description="Filter by department")] = None,
    from_: Annotated[datetime | None, Query(alias="from", description="Start timestamp")] = None,
    to: Annotated[datetime | None, Query(alias="to", description="End timestamp")] = None,
) -> CostBreakdown:
    """Retrieve model cost and token breakdown."""
    database = request.app.state.database
    principal = getattr(request.state, "principal", None)
    tenant_id = getattr(principal, "tenant_id", "tenant-1") or "tenant-1"

    async with database.session() as session:
        usage_repo = UsageEventRepository(session)
        cost_repo = CostEventRepository(session)
        service = CostAggregationService(usage_repo, cost_repo)

        return await service.get_cost_by_model(
            tenant_id,
            plant_id=plant_id,
            department_id=department_id,
            from_ts=from_,
            to_ts=to,
        )


@router.get(
    "/by-agent",
    summary="Get cost grouped by agent",
    response_model=CostBreakdown,
    dependencies=_AUTH_DEPS,
)
async def get_cost_by_agent(
    request: Request,
    plant_id: Annotated[str | None, Query(alias="plant_id", description="Filter by plant")] = None,
    department_id: Annotated[str | None, Query(alias="department_id", description="Filter by department")] = None,
    from_: Annotated[datetime | None, Query(alias="from", description="Start timestamp")] = None,
    to: Annotated[datetime | None, Query(alias="to", description="End timestamp")] = None,
) -> CostBreakdown:
    """Retrieve agent cost and token breakdown."""
    database = request.app.state.database
    principal = getattr(request.state, "principal", None)
    tenant_id = getattr(principal, "tenant_id", "tenant-1") or "tenant-1"

    async with database.session() as session:
        usage_repo = UsageEventRepository(session)
        cost_repo = CostEventRepository(session)
        service = CostAggregationService(usage_repo, cost_repo)

        return await service.get_cost_by_agent(
            tenant_id,
            plant_id=plant_id,
            department_id=department_id,
            from_ts=from_,
            to_ts=to,
        )


@router.get(
    "/by-plant",
    summary="Get cost grouped by plant",
    response_model=CostBreakdown,
    dependencies=_AUTH_DEPS,
)
async def get_cost_by_plant(
    request: Request,
    from_: Annotated[datetime | None, Query(alias="from", description="Start timestamp")] = None,
    to: Annotated[datetime | None, Query(alias="to", description="End timestamp")] = None,
) -> CostBreakdown:
    """Retrieve plant cost and token breakdown."""
    database = request.app.state.database
    principal = getattr(request.state, "principal", None)
    tenant_id = getattr(principal, "tenant_id", "tenant-1") or "tenant-1"

    async with database.session() as session:
        usage_repo = UsageEventRepository(session)
        cost_repo = CostEventRepository(session)
        service = CostAggregationService(usage_repo, cost_repo)

        return await service.get_cost_by_plant(
            tenant_id,
            from_ts=from_,
            to_ts=to,
        )


@router.get(
    "/trend",
    summary="Get historical cost trend",
    response_model=CostTrend,
    dependencies=_AUTH_DEPS,
)
async def get_cost_trend(
    request: Request,
    plant_id: Annotated[str | None, Query(alias="plant_id", description="Filter by plant")] = None,
    department_id: Annotated[str | None, Query(alias="department_id", description="Filter by department")] = None,
    from_: Annotated[datetime | None, Query(alias="from", description="Start timestamp")] = None,
    to: Annotated[datetime | None, Query(alias="to", description="End timestamp")] = None,
    granularity: Annotated[
        Literal["hour", "day", "week", "month"],
        Query(description="Time bucket aggregation granularity"),
    ] = "day",
) -> CostTrend:
    """Retrieve historical cost trend across time buckets."""
    database = request.app.state.database
    principal = getattr(request.state, "principal", None)
    tenant_id = getattr(principal, "tenant_id", "tenant-1") or "tenant-1"

    async with database.session() as session:
        usage_repo = UsageEventRepository(session)
        cost_repo = CostEventRepository(session)
        service = CostAggregationService(usage_repo, cost_repo)

        return await service.get_cost_trend(
            tenant_id,
            plant_id=plant_id,
            department_id=department_id,
            from_ts=from_,
            to_ts=to,
            granularity=granularity,
        )
