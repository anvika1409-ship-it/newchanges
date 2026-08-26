"""Startup route-protection tests.

The audit exists so an endpoint cannot ship without authentication. These tests
assert it actually catches the omission — a check that never fails is worse than
no check, because it is believed.

Never "fix" a failure here by adding the offending path to PUBLIC_OPERATIONS.
That allowlist is for operations API_CONTRACT.yaml declares without a
``security:`` requirement, and it currently contains every such operation.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI

from app.core.config import Settings
from app.main import create_app
from app.security.dependencies import (
    CurrentPrincipal,
    RequirePermission,
    RequireRoles,
    RequireScope,
)
from app.security.permissions import Permission
from app.security.principal import Principal, Role
from app.security.route_protection import (
    PUBLIC_OPERATIONS,
    UnprotectedRouteError,
    unprotected_routes,
    verify_route_protection,
)
from app.security.scope import AuthorizedScope

PREFIX = "/api/v1"


def _app_with(router: APIRouter) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix=PREFIX)
    return app


# ===========================================================================
# The check catches an omission
# ===========================================================================
def test_an_unguarded_route_aborts_startup() -> None:
    router = APIRouter()

    @router.get("/budgets")
    async def leaky() -> dict[str, str]:
        return {"secret": "every tenant's budgets"}

    with pytest.raises(UnprotectedRouteError, match="GET /api/v1/budgets"):
        verify_route_protection(_app_with(router), api_prefix=PREFIX)


def test_every_offending_operation_is_reported_at_once() -> None:
    """One startup attempt should list them all, not one per run."""
    router = APIRouter()

    @router.get("/a")
    async def a() -> dict[str, str]:
        return {}

    @router.post("/b")
    async def b() -> dict[str, str]:
        return {}

    offenders = unprotected_routes(_app_with(router), api_prefix=PREFIX)
    assert offenders == [f"GET {PREFIX}/a", f"POST {PREFIX}/b"]


def test_a_route_guarded_only_by_a_non_security_dependency_still_fails() -> None:
    """Having *a* dependency is not the same as having authentication."""

    async def unrelated() -> str:
        return "not a security check"

    router = APIRouter()

    @router.get("/budgets", dependencies=[Depends(unrelated)])
    async def leaky() -> dict[str, str]:
        return {}

    with pytest.raises(UnprotectedRouteError):
        verify_route_protection(_app_with(router), api_prefix=PREFIX)


# ===========================================================================
# The check accepts every real guard
# ===========================================================================
def test_current_principal_satisfies_the_check() -> None:
    router = APIRouter()

    @router.get("/whoami")
    async def whoami(principal: CurrentPrincipal) -> dict[str, str]:
        return {"subject": principal.subject}

    verify_route_protection(_app_with(router), api_prefix=PREFIX)


def test_require_permission_satisfies_the_check() -> None:
    router = APIRouter()

    @router.get(
        "/budgets", dependencies=[Depends(RequirePermission(Permission.BUDGET_READ))]
    )
    async def budgets() -> dict[str, str]:
        return {}

    verify_route_protection(_app_with(router), api_prefix=PREFIX)


def test_require_roles_satisfies_the_check() -> None:
    router = APIRouter()

    @router.get("/budgets")
    async def budgets(
        principal: Annotated[Principal, Depends(RequireRoles(Role.ADMIN))],
    ) -> dict[str, str]:
        return {"subject": principal.subject}

    verify_route_protection(_app_with(router), api_prefix=PREFIX)


def test_require_scope_satisfies_the_check() -> None:
    router = APIRouter()

    @router.get("/cost/summary")
    async def summary(
        scope: Annotated[AuthorizedScope, Depends(RequireScope(Permission.COST_READ))],
    ) -> dict[str, str]:
        return {"tenant_id": scope.tenant_id}

    verify_route_protection(_app_with(router), api_prefix=PREFIX)


def test_a_guard_nested_behind_another_dependency_is_found() -> None:
    """The walk is recursive, so a future wrapper built on CurrentPrincipal
    still counts as authentication."""

    async def wrapper(principal: CurrentPrincipal) -> Principal:
        return principal

    router = APIRouter()

    @router.get("/budgets", dependencies=[Depends(wrapper)])
    async def budgets() -> dict[str, str]:
        return {}

    verify_route_protection(_app_with(router), api_prefix=PREFIX)


# ===========================================================================
# Public operations
# ===========================================================================
def test_public_operations_match_the_contract() -> None:
    """API_CONTRACT.yaml declares exactly two operations with no `security:`
    requirement. Anything else in this set is an unreviewed exemption."""
    assert frozenset({"/health", "/ready"}) == PUBLIC_OPERATIONS


def test_a_listed_public_operation_is_exempt() -> None:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "alive"}

    verify_route_protection(_app_with(router), api_prefix=PREFIX)


def test_the_exemption_is_by_exact_path_not_prefix() -> None:
    """`/health-and-all-budgets` must not inherit `/health`'s exemption."""
    router = APIRouter()

    @router.get("/health-and-all-budgets")
    async def sneaky() -> dict[str, str]:
        return {}

    with pytest.raises(UnprotectedRouteError):
        verify_route_protection(_app_with(router), api_prefix=PREFIX)


def test_routes_outside_the_api_prefix_are_not_audited() -> None:
    """Docs and schema routes are governed by the debug flag, not by RBAC."""
    app = FastAPI()

    @app.get("/internal-probe")
    async def probe() -> dict[str, str]:
        return {}

    verify_route_protection(app, api_prefix=PREFIX)


# ===========================================================================
# The real application
# ===========================================================================
def test_the_application_passes_its_own_audit(settings: Settings) -> None:
    """create_app runs this itself; asserting it here makes the guarantee
    explicit rather than incidental."""
    app = create_app(settings)
    assert unprotected_routes(app, api_prefix=settings.api_v1_prefix) == []


def test_adding_an_unguarded_route_to_the_real_app_would_be_caught(
    settings: Settings,
) -> None:
    app = create_app(settings)

    @app.get(f"{settings.api_v1_prefix}/budgets")
    async def leaky() -> dict[str, str]:
        return {}

    with pytest.raises(UnprotectedRouteError):
        verify_route_protection(app, api_prefix=settings.api_v1_prefix)
