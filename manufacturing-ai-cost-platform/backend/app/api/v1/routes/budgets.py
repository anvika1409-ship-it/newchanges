"""Budget endpoints.

Implements ``GET /budgets``, ``POST /budgets``, ``PATCH /budgets/{id}`` and
``GET /budgets/status`` exactly as declared in API_CONTRACT.yaml.

Authorization is two-level (SECURITY.md section 4):

* reading requires BUDGET_READ, writing requires BUDGET_MANAGE — endpoint level;
* the budget's own scope is then checked against the caller's assignments —
  resource level. This is the worked example in SECURITY.md section 4: a plant
  manager must not see, or create, another plant's budget.

The resource-level check maps a budget's ``scope_type``/``scope_id`` onto the
tenant/plant/department triple authorization understands. A WORKLOAD, AGENT or
MODEL budget has no plant column of its own, so it is authorized at tenant level
— only a caller with tenant-wide reach may touch one. That is the conservative
reading; narrowing it further needs the parent lookup that arrives with the
workload and agent endpoints.

``tenant_id`` is never read from the request. It comes from the authenticated
principal (SECURITY.md section 5).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.v1.schemas.budgets import (
    BudgetCreateRequest,
    BudgetListResponse,
    BudgetResponse,
    BudgetStatusListResponse,
    BudgetStatusResponse,
    BudgetUpdateRequest,
    PageInfo,
)
from app.core.errors import BadRequestError, NotFoundError
from app.core.logging import get_logger
from app.db.models.governance import Budget
from app.repositories.budget_repository import BudgetRepository
from app.repositories.cost_repository import CostAggregationRepository
from app.security.authorization import (
    authorize_resource_permission,
    can_access_resource_permission,
)
from app.security.dependencies import RequirePermission
from app.security.permissions import Permission
from app.security.principal import Principal, ResourceScope
from app.services.budget_service import BudgetService
from app.services.currency import CurrencyConverter

logger = get_logger(__name__)

router = APIRouter(tags=["Budgets"])

BudgetReader = Annotated[Principal, Depends(RequirePermission(Permission.BUDGET_READ))]
BudgetManager = Annotated[
    Principal, Depends(RequirePermission(Permission.BUDGET_MANAGE))
]


async def get_budget_service(request: Request) -> AsyncIterator[BudgetService]:
    """Build the budget service for this request, bound to a session."""
    database = request.app.state.database
    settings = request.app.state.settings
    async with database.session() as session:
        yield BudgetService(
            BudgetRepository(session),
            CostAggregationRepository(session),
            converter=CurrencyConverter(
                base_currency=settings.platform_base_currency,
                rates=settings.currency_rates,
            ),
        )


async def get_budget_repository(request: Request) -> AsyncIterator[BudgetRepository]:
    """A repository-only dependency, for the endpoints that do no evaluation."""
    database = request.app.state.database
    async with database.session() as session:
        yield BudgetRepository(session)


BudgetSvc = Annotated[BudgetService, Depends(get_budget_service)]
Budgets = Annotated[BudgetRepository, Depends(get_budget_repository)]


def _resource_scope(principal: Principal, budget: Budget) -> ResourceScope:
    """Map a budget's scope onto the triple authorization understands.

    PLANT and DEPARTMENT budgets carry their plant or department directly.
    Everything else — ENTERPRISE, TENANT, WORKLOAD, AGENT, MODEL — authorizes at
    tenant level, because the budget row holds no plant column to check against
    and inventing one from a name would be a guess.
    """
    tenant_id = budget.tenant_id or principal.tenant_id
    match budget.scope_type:
        case "PLANT":
            return ResourceScope(tenant_id=tenant_id, plant_id=budget.scope_id)
        case "DEPARTMENT":
            return ResourceScope(tenant_id=tenant_id, department_id=budget.scope_id)
        case _:
            return ResourceScope(tenant_id=tenant_id)


def _authorize_scope_pair(
    principal: Principal, scope_type: str, scope_id: str, permission: Permission
) -> None:
    """Resource-level check for a budget that does not exist yet.

    ``POST /budgets`` must be authorized against the scope the caller is
    *proposing*, before any row exists. Without this, a plant manager could
    create a budget over another plant and then legitimately read it back.
    """
    placeholder = Budget(
        id="",
        tenant_id=principal.tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
        amount=1.0,
        currency="",
        period="MONTHLY",
        warning_threshold_percent=80.0,
        critical_threshold_percent=95.0,
        status="ACTIVE",
    )
    authorize_resource_permission(
        principal, _resource_scope(principal, placeholder), permission
    )


@router.get("/budgets", summary="List budgets", response_model=BudgetListResponse)
async def list_budgets(
    principal: BudgetReader,
    budgets: Budgets,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BudgetListResponse:
    """List the caller's tenant's budgets.

    Rows the caller may not reach at resource level are filtered out. ``total``
    counts the tenant's budgets before that filter, matching what the repository
    paged over; a per-row count would require reading every row to produce it.
    """
    rows = await budgets.list_by_tenant(principal.tenant_id, limit=limit, offset=offset)
    total = await budgets.count_by_tenant(principal.tenant_id)

    visible = [row for row in rows if _can_read(principal, row)]
    return BudgetListResponse(
        items=[BudgetResponse.from_row(row) for row in visible],
        page=PageInfo(total=total, limit=limit, offset=offset),
    )


def _can_read(principal: Principal, budget: Budget) -> bool:
    """Resource-level visibility test for one budget row."""
    return can_access_resource_permission(
        principal, _resource_scope(principal, budget), Permission.BUDGET_READ
    )


@router.post(
    "/budgets",
    summary="Create a budget",
    response_model=BudgetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_budget(
    payload: BudgetCreateRequest,
    principal: BudgetManager,
    budgets: Budgets,
) -> BudgetResponse:
    """Create a budget at a scope the caller may manage.

    Raises:
        BadRequestError: a budget already exists at this scope. The evaluation
            path expects at most one active budget per scope; two would make the
            decision depend on which row was read first.
    """
    _authorize_scope_pair(
        principal, payload.scope_type, payload.scope_id, Permission.BUDGET_MANAGE
    )

    existing = await budgets.get_for_scope(
        principal.tenant_id, payload.scope_type, payload.scope_id
    )
    if existing is not None:
        raise BadRequestError(
            "An active budget already exists for this scope. Update it instead.",
            details={"budget_id": existing.id},
        )

    now = datetime.now(UTC)
    budget = Budget(
        id=str(uuid.uuid4()),
        tenant_id=principal.tenant_id,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
        amount=payload.amount,
        currency=payload.currency.upper(),
        period=payload.period,
        warning_threshold_percent=payload.warning_threshold_percent,
        critical_threshold_percent=payload.critical_threshold_percent,
        status="ACTIVE",
        created_at=now,
        updated_at=now,
    )
    await budgets.add(budget)

    # SECURITY.md section 16 lists "budget changed" as an auditable event. The
    # audit_events table exists; the service that writes it does not yet, so
    # this is logged with the same fields until that lands.
    logger.info(
        "budget_created",
        extra={
            "budget_id": budget.id,
            "scope_type": budget.scope_type,
            "scope_id": budget.scope_id,
            "actor": principal.subject,
        },
    )
    return BudgetResponse.from_row(budget)


@router.patch(
    "/budgets/{id}", summary="Update a budget", response_model=BudgetResponse
)
async def update_budget(
    id: str,  # noqa: A002 - the contract names this path parameter "id"
    payload: BudgetUpdateRequest,
    principal: BudgetManager,
    budgets: Budgets,
) -> BudgetResponse:
    """Update a budget the caller may manage.

    A budget in another tenant is a 404, not a 403 — a 403 would confirm the
    row exists across a tenant boundary.
    """
    budget = await budgets.get_by_id(id, principal.tenant_id)
    if budget is None:
        raise NotFoundError()

    authorize_resource_permission(
        principal, _resource_scope(principal, budget), Permission.BUDGET_MANAGE
    )

    before = {
        "amount": budget.amount,
        "period": budget.period,
        "warning_threshold_percent": budget.warning_threshold_percent,
        "critical_threshold_percent": budget.critical_threshold_percent,
        "status": budget.status,
    }

    changes = payload.model_dump(exclude_unset=True, exclude={"reason"})
    for field_name, value in changes.items():
        setattr(budget, field_name, value)

    warning = budget.warning_threshold_percent
    critical = budget.critical_threshold_percent
    if warning > critical:
        raise BadRequestError(
            "warning_threshold_percent must not exceed critical_threshold_percent.",
            details={
                "warning_threshold_percent": warning,
                "critical_threshold_percent": critical,
            },
        )

    await budgets.update(budget)

    logger.info(
        "budget_updated",
        extra={
            "budget_id": budget.id,
            "actor": principal.subject,
            "before_state": before,
            "after_state": changes,
            "change_reason": payload.reason,
        },
    )
    return BudgetResponse.from_row(budget)


@router.get(
    "/budgets/status",
    summary="Get budget status",
    response_model=BudgetStatusListResponse,
)
async def budget_status(
    principal: BudgetReader,
    service: BudgetSvc,
    budgets: Budgets,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BudgetStatusListResponse:
    """Consumption and threshold state for each visible budget.

    ``threshold_state`` is NORMAL / WARNING / CRITICAL / EXCEEDED, or null when
    the budget could not be compared against spend at all. Null is not a passing
    state — ``unevaluable_reason`` says why, and budget evaluation turns the
    same condition into REQUIRE_APPROVAL rather than ALLOW.
    """
    rows = await budgets.list_by_tenant(principal.tenant_id, limit=limit, offset=offset)
    total = await budgets.count_by_tenant(principal.tenant_id)
    visible = [row for row in rows if _can_read(principal, row)]

    results = await service.status_for_budgets(visible)
    return BudgetStatusListResponse(
        items=[BudgetStatusResponse.from_result(result) for result in results],
        page=PageInfo(total=total, limit=limit, offset=offset),
    )
