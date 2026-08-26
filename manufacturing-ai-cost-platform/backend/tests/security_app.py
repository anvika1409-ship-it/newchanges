"""Test fixture app exercising the security dependencies.

These routes exist only in the test suite. They are deliberately **not** added
to the real application: inventing endpoints that API_CONTRACT.yaml does not
declare is forbidden (AI_DEVELOPMENT_RULES.md sections 2 and 18), and
``test_startup`` asserts the live app exposes nothing but /health and /ready.

The routes here are wired from the same dependencies production code uses, so
they test the real authorization path rather than a parallel implementation.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Query

from app.core.config import Settings
from app.core.errors import register_exception_handlers
from app.core.middleware import RequestIDMiddleware
from app.security.authorization import (
    authorize_resource,
    authorize_resource_permission,
    reject_client_tenant_override,
)
from app.security.dependencies import (
    CurrentPrincipal,
    RequirePermission,
    RequireRoles,
    RequireScope,
)
from app.security.identity import build_identity_adapter
from app.security.permissions import Permission
from app.security.principal import Principal, ResourceScope, Role
from app.security.scope import AuthorizedScope

router = APIRouter()


@router.get("/whoami")
async def whoami(principal: CurrentPrincipal) -> dict[str, object]:
    """Authentication only — any valid token reaches this."""
    return {
        "subject": principal.subject,
        "tenant_id": principal.tenant_id,
        "roles": sorted(str(r) for r in principal.roles),
    }


@router.get(
    "/finops-only",
    dependencies=[Depends(RequireRoles(Role.ADMIN, Role.FINOPS_MANAGER))],
)
async def finops_only() -> dict[str, str]:
    """Endpoint-level RBAC only."""
    return {"status": "ok"}


@router.get("/budgets/{budget_id}")
async def read_budget(
    budget_id: str,
    principal: Annotated[
        Principal, Depends(RequireRoles(Role.ADMIN, Role.FINOPS_MANAGER, Role.PLANT_MANAGER))
    ],
    tenant_id: Annotated[str, Query()],
    plant_id: Annotated[str | None, Query()] = None,
    department_id: Annotated[str | None, Query()] = None,
) -> dict[str, str]:
    """Endpoint RBAC *and* resource-level scope.

    ``tenant_id`` and ``plant_id`` stand in for the ownership columns that would
    be read from the stored record. The endpoint guard has already run; this is
    the second half of the SECURITY.md section 4 requirement.
    """
    resource = ResourceScope(
        tenant_id=tenant_id,
        plant_id=plant_id,
        department_id=department_id,
    )
    authorize_resource(
        principal,
        resource,
        Role.ADMIN,
        Role.FINOPS_MANAGER,
        Role.PLANT_MANAGER,
    )
    return {"budget_id": budget_id, "tenant_id": tenant_id}


@router.get("/cost-summary")
async def cost_summary(
    principal: CurrentPrincipal,
    tenant_id: Annotated[str | None, Query()] = None,
) -> dict[str, str]:
    """Tenant resolution: a client-supplied tenant is never trusted."""
    effective = reject_client_tenant_override(principal, tenant_id)
    return {"tenant_id": effective}


@router.get(
    "/budget-manage",
    dependencies=[Depends(RequirePermission(Permission.BUDGET_MANAGE))],
)
async def budget_manage() -> dict[str, str]:
    """Endpoint-level RBAC expressed as a permission."""
    return {"status": "ok"}


@router.get(
    "/model-manage",
    dependencies=[Depends(RequirePermission(Permission.MODEL_MANAGE))],
)
async def model_manage() -> dict[str, str]:
    """A different permission, so role separation is observable."""
    return {"status": "ok"}


@router.get("/budgets-by-permission/{budget_id}")
async def read_budget_by_permission(
    budget_id: str,
    principal: Annotated[Principal, Depends(RequirePermission(Permission.BUDGET_READ))],
    tenant_id: Annotated[str, Query()],
    plant_id: Annotated[str | None, Query()] = None,
    department_id: Annotated[str | None, Query()] = None,
) -> dict[str, str]:
    """Endpoint RBAC *and* resource-level scope, both driven by a permission.

    ``tenant_id`` and ``plant_id`` stand in for the ownership columns that would
    be read from the stored record.
    """
    authorize_resource_permission(
        principal,
        ResourceScope(
            tenant_id=tenant_id, plant_id=plant_id, department_id=department_id
        ),
        Permission.BUDGET_READ,
    )
    return {"budget_id": budget_id, "tenant_id": tenant_id}


@router.get("/budget-write/{budget_id}")
async def write_budget(
    budget_id: str,
    principal: Annotated[
        Principal, Depends(RequirePermission(Permission.BUDGET_MANAGE))
    ],
    tenant_id: Annotated[str, Query()],
    plant_id: Annotated[str | None, Query()] = None,
) -> dict[str, str]:
    """A write, so a read-only assignment elsewhere must not authorize it."""
    authorize_resource_permission(
        principal,
        ResourceScope(tenant_id=tenant_id, plant_id=plant_id),
        Permission.BUDGET_MANAGE,
    )
    return {"budget_id": budget_id}


@router.get("/cost-scope")
async def cost_scope(
    scope: Annotated[AuthorizedScope, Depends(RequireScope(Permission.COST_READ))],
) -> dict[str, object]:
    """Collection query: the caller receives a constraint, not a row set.

    The response echoes the resolved branches so a test can assert exactly what
    a repository would be told to filter on.
    """
    return {
        "tenant_id": scope.tenant_id,
        "is_tenant_wide": scope.is_tenant_wide,
        "branches": [
            {
                "tenant_id": branch.tenant_id,
                "plant_id": branch.plant_id,
                "department_id": branch.department_id,
            }
            for branch in scope.branches
        ],
    }


@router.get("/audit-scope")
async def audit_scope(
    scope: Annotated[AuthorizedScope, Depends(RequireScope(Permission.AUDIT_READ))],
) -> dict[str, object]:
    """A scope for a permission most roles do not hold."""
    return {"tenant_id": scope.tenant_id}


def create_security_test_app(settings: Settings) -> FastAPI:
    """Build a minimal app carrying the real security wiring."""
    app = FastAPI()
    app.state.settings = settings
    app.state.identity_adapter = build_identity_adapter(settings)
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)
    app.include_router(router)
    return app
