"""Cost endpoints.

Implements ``/cost/summary``, ``/cost/by-model``, ``/cost/by-agent``,
``/cost/by-plant`` and ``/cost/trend`` exactly as declared in API_CONTRACT.yaml.

Authorization is two-level (SECURITY.md section 4). ``RequireScope`` supplies
both halves at once for a collection read: it enforces COST_READ at the endpoint
and resolves the caller's tenant/plant/department constraint, which the
repository turns into a WHERE clause. Rows outside the caller's scope are never
read, so a plant manager's totals cannot include another plant's spend even
though the SQL is shared.

``/cost/by-plant`` and the endpoints' query parameters follow the contract
exactly: only ``/cost/summary``, ``/cost/by-model``, ``/cost/by-agent`` and
``/cost/trend`` declare ``plant_id`` and ``department_id``, so only those use
``RequireScope``. ``/cost/by-plant`` declares neither and resolves its scope from
the principal alone — adding the parameters there would put undeclared query
parameters into the OpenAPI schema.
"""

from __future__ import annotations

<<<<<<< HEAD
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.v1.schemas.costs import (
    CostBreakdownItemResponse,
    CostBreakdownResponse,
    CostSummaryResponse,
    CostTrendPointResponse,
    CostTrendResponse,
)
from app.repositories.cost_repository import (
    BreakdownDimension,
    CostAggregationRepository,
    Granularity,
)
from app.security.dependencies import CurrentPrincipal, RequireScope
from app.security.permissions import Permission
from app.security.scope import AuthorizedScope, resolve_authorized_scope
from app.services.cost_aggregation import CostAggregationService

router = APIRouter(tags=["Costs"])

CostScope = Annotated[AuthorizedScope, Depends(RequireScope(Permission.COST_READ))]

#: The contract's `from` query parameter. `from` is a Python keyword, so the
#: handler parameter is renamed and aliased back onto the contract's spelling.
FromTs = Annotated[datetime | None, Query(alias="from")]
ToTs = Annotated[datetime | None, Query(alias="to")]


async def get_cost_service(request: Request) -> AsyncIterator[CostAggregationService]:
    """Build the aggregation service for this request, bound to a session."""
    database = request.app.state.database
    settings = request.app.state.settings
    async with database.session() as session:
        yield CostAggregationService(
            CostAggregationRepository(session),
            base_currency=settings.platform_base_currency,
        )


CostService = Annotated[CostAggregationService, Depends(get_cost_service)]


@router.get(
    "/cost/summary",
    summary="Get current cost summary",
    response_model=CostSummaryResponse,
)
async def cost_summary(
    service: CostService,
    scope: CostScope,
    from_ts: FromTs = None,
    to_ts: ToTs = None,
) -> CostSummaryResponse:
    """Aggregate spend across everything the caller may see.

    ``budget_consumed_percent`` and ``forecast_month_end_cost`` are null here.
    The first needs a governing budget the contract gives no parameter to
    select; the second is a FORECAST owned by the intelligence layer. Returning
    a plausible-looking number for either would be fabricating one.
    """
    result = await service.summary(scope, from_ts=from_ts, to_ts=to_ts)
    return CostSummaryResponse.from_result(result)


async def _breakdown(
    service: CostAggregationService,
    scope: AuthorizedScope,
    dimension: BreakdownDimension,
    from_ts: datetime | None,
    to_ts: datetime | None,
) -> CostBreakdownResponse:
    entries = await service.breakdown(scope, dimension, from_ts=from_ts, to_ts=to_ts)
    return CostBreakdownResponse(
        dimension=dimension.value,
        items=[CostBreakdownItemResponse.from_entry(entry) for entry in entries],
    )


@router.get(
    "/cost/by-model",
    summary="Get cost grouped by model",
    response_model=CostBreakdownResponse,
)
async def cost_by_model(
    service: CostService,
    scope: CostScope,
    from_ts: FromTs = None,
    to_ts: ToTs = None,
) -> CostBreakdownResponse:
    return await _breakdown(service, scope, BreakdownDimension.MODEL, from_ts, to_ts)


@router.get(
    "/cost/by-agent",
    summary="Get cost grouped by agent",
    response_model=CostBreakdownResponse,
)
async def cost_by_agent(
    service: CostService,
    scope: CostScope,
    from_ts: FromTs = None,
    to_ts: ToTs = None,
) -> CostBreakdownResponse:
    return await _breakdown(service, scope, BreakdownDimension.AGENT, from_ts, to_ts)


@router.get(
    "/cost/by-plant",
    summary="Get cost grouped by plant",
    response_model=CostBreakdownResponse,
)
async def cost_by_plant(
    service: CostService,
    principal: CurrentPrincipal,
    from_ts: FromTs = None,
    to_ts: ToTs = None,
) -> CostBreakdownResponse:
    """Spend grouped by plant.

    The contract declares no ``plant_id`` parameter here — filtering a
    by-plant breakdown to one plant is what ``/cost/summary?plant_id=`` is for —
    so the scope comes from the principal alone. A caller scoped to one plant
    sees exactly one row.
    """
    scope = resolve_authorized_scope(principal, Permission.COST_READ)
    return await _breakdown(service, scope, BreakdownDimension.PLANT, from_ts, to_ts)


@router.get(
    "/cost/trend",
    summary="Get historical cost trend",
    response_model=CostTrendResponse,
)
async def cost_trend(
    service: CostService,
    scope: CostScope,
    from_ts: FromTs = None,
    to_ts: ToTs = None,
    granularity: Annotated[Granularity, Query()] = Granularity.DAY,
) -> CostTrendResponse:
    """Spend bucketed over time, oldest bucket first."""
    entries = await service.trend(scope, granularity, from_ts=from_ts, to_ts=to_ts)
    return CostTrendResponse(
        granularity=granularity.value,
        points=[CostTrendPointResponse.from_entry(entry) for entry in entries],
    )
=======
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
>>>>>>> 403f410fed5d9b2e3f833ee511fd3244d2938180
