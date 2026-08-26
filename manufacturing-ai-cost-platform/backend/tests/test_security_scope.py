"""Scope resolution tests: tenant isolation, plant scope, department scope.

Covers the required cases for a collection query: unauthenticated request,
invalid token, expired token, wrong role, cross-tenant access and authorized
access — plus the narrowing rules for the contract's ``plant_id`` and
``department_id`` query parameters.

Every negative assertion here is a security boundary. If one fails, fix the
authorization code — never the expectation.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.errors import ForbiddenError
from app.security.authorization import (
    CrossTenantAccessError,
    authorize_resource_permission,
)
from app.security.identity import DevelopmentIdentityAdapter
from app.security.permissions import Permission
from app.security.principal import (
    Principal,
    ResourceScope,
    Role,
    RoleAssignment,
    ScopeType,
)
from app.security.scope import (
    AuthorizedScope,
    ScopeConstraint,
    resolve_authorized_scope,
)
from tests.security_app import create_security_test_app

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
PLANT_1 = "plant-1"
PLANT_2 = "plant-2"
DEPT_1 = "dept-1"
DEPT_2 = "dept-2"


@pytest.fixture
def adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_security_test_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _principal(*assignments: RoleAssignment, tenant: str = TENANT_A) -> Principal:
    return Principal(subject="user-1", tenant_id=tenant, assignments=assignments)


# ===========================================================================
# Tenant isolation
# ===========================================================================
def test_scope_is_always_the_authenticated_tenant() -> None:
    scope = resolve_authorized_scope(
        _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A)),
        Permission.COST_READ,
    )
    assert scope.tenant_id == TENANT_A
    assert all(branch.tenant_id == TENANT_A for branch in scope.branches)


def test_client_supplied_tenant_override_is_refused() -> None:
    """SECURITY.md section 5: never trust a client-provided tenant id."""
    principal = _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A))
    with pytest.raises(CrossTenantAccessError):
        resolve_authorized_scope(
            principal, Permission.COST_READ, requested_tenant_id=TENANT_B
        )


def test_echoing_own_tenant_is_permitted() -> None:
    principal = _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A))
    scope = resolve_authorized_scope(
        principal, Permission.COST_READ, requested_tenant_id=TENANT_A
    )
    assert scope.is_tenant_wide


def test_tenant_assignment_over_another_tenant_grants_nothing() -> None:
    """A validly signed token asserting TENANT scope over someone else's tenant.

    The claim is authentic but not authoritative: the assignment contributes no
    branch, so there is nothing left to authorize.
    """
    principal = _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_B))
    with pytest.raises(ForbiddenError):
        resolve_authorized_scope(principal, Permission.COST_READ)


def test_a_scope_never_covers_another_tenants_record() -> None:
    scope = resolve_authorized_scope(
        _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A)),
        Permission.COST_READ,
    )
    assert scope.covers(ResourceScope(TENANT_A, plant_id=PLANT_1))
    assert not scope.covers(ResourceScope(TENANT_B, plant_id=PLANT_1))


# ===========================================================================
# Permission gating
# ===========================================================================
def test_scope_requires_the_permission() -> None:
    principal = _principal(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A))
    with pytest.raises(ForbiddenError):
        resolve_authorized_scope(principal, Permission.AUDIT_READ)


def test_only_granting_assignments_contribute_branches() -> None:
    """VIEWER on plant-2 must not widen a FinOps-only scope to plant-2."""
    principal = _principal(
        RoleAssignment(Role.FINOPS_MANAGER, ScopeType.PLANT, PLANT_1),
        RoleAssignment(Role.VIEWER, ScopeType.PLANT, PLANT_2),
    )

    audit = resolve_authorized_scope(principal, Permission.AUDIT_READ)
    assert audit.plant_ids == {PLANT_1}

    cost = resolve_authorized_scope(principal, Permission.COST_READ)
    assert cost.plant_ids == {PLANT_1, PLANT_2}


# ===========================================================================
# Plant scope
# ===========================================================================
def test_plant_scoped_role_yields_a_plant_constraint() -> None:
    scope = resolve_authorized_scope(
        _principal(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1)),
        Permission.COST_READ,
    )
    assert not scope.is_tenant_wide
    assert scope.plant_ids == {PLANT_1}
    assert scope.department_ids is None


def test_plant_filter_outside_scope_is_refused_not_ignored() -> None:
    """Silently dropping the filter would answer a probe for plant-2 with
    plant-1's data, which reads as success and hides the attempt."""
    principal = _principal(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1))
    with pytest.raises(ForbiddenError):
        resolve_authorized_scope(
            principal, Permission.COST_READ, requested_plant_id=PLANT_2
        )


def test_plant_filter_within_scope_narrows() -> None:
    principal = _principal(
        RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),
        RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_2),
    )
    scope = resolve_authorized_scope(
        principal, Permission.COST_READ, requested_plant_id=PLANT_1
    )
    assert scope.plant_ids == {PLANT_1}


def test_tenant_wide_role_narrows_to_a_requested_plant() -> None:
    principal = _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A))
    scope = resolve_authorized_scope(
        principal, Permission.COST_READ, requested_plant_id=PLANT_2
    )
    assert scope.plant_ids == {PLANT_2}
    assert not scope.is_tenant_wide


def test_plant_scope_does_not_cover_a_tenant_wide_record() -> None:
    """An enterprise budget has no plant, so plant scope does not reach it."""
    scope = resolve_authorized_scope(
        _principal(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1)),
        Permission.BUDGET_READ,
    )
    assert not scope.covers(ResourceScope(TENANT_A))


# ===========================================================================
# Department scope
# ===========================================================================
def test_department_scoped_role_yields_a_department_constraint() -> None:
    scope = resolve_authorized_scope(
        _principal(RoleAssignment(Role.ANALYST, ScopeType.DEPARTMENT, DEPT_1)),
        Permission.COST_READ,
    )
    assert scope.department_ids == {DEPT_1}
    assert scope.plant_ids is None
    assert scope.covers(ResourceScope(TENANT_A, plant_id=PLANT_1, department_id=DEPT_1))
    assert not scope.covers(ResourceScope(TENANT_A, plant_id=PLANT_1, department_id=DEPT_2))


def test_department_filter_outside_scope_is_refused() -> None:
    principal = _principal(RoleAssignment(Role.ANALYST, ScopeType.DEPARTMENT, DEPT_1))
    with pytest.raises(ForbiddenError):
        resolve_authorized_scope(
            principal, Permission.COST_READ, requested_department_id=DEPT_2
        )


def test_department_scope_does_not_grant_the_whole_plant() -> None:
    scope = resolve_authorized_scope(
        _principal(RoleAssignment(Role.ANALYST, ScopeType.DEPARTMENT, DEPT_1)),
        Permission.COST_READ,
    )
    assert not scope.covers(ResourceScope(TENANT_A, plant_id=PLANT_1))


def test_department_filter_from_a_plant_scoped_caller_keeps_the_plant_bound() -> None:
    """A plant manager filtering by department stays inside their plant.

    The parent lookup that would reject a foreign department up front needs the
    departments table, which does not exist yet. The conjunction is safe without
    it: the plant constraint survives, so a department in another plant matches
    no rows.
    """
    principal = _principal(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1))
    scope = resolve_authorized_scope(
        principal, Permission.COST_READ, requested_department_id=DEPT_2
    )

    assert scope.branches == (
        ScopeConstraint(tenant_id=TENANT_A, plant_id=PLANT_1, department_id=DEPT_2),
    )
    # A department row in another plant is still out of reach.
    assert not scope.covers(ResourceScope(TENANT_A, plant_id=PLANT_2, department_id=DEPT_2))
    assert scope.covers(ResourceScope(TENANT_A, plant_id=PLANT_1, department_id=DEPT_2))


def test_both_filters_apply_together() -> None:
    principal = _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A))
    scope = resolve_authorized_scope(
        principal,
        Permission.COST_READ,
        requested_plant_id=PLANT_1,
        requested_department_id=DEPT_1,
    )
    assert scope.branches == (
        ScopeConstraint(tenant_id=TENANT_A, plant_id=PLANT_1, department_id=DEPT_1),
    )


# ===========================================================================
# Mixed scopes — the case a single filter cannot express
# ===========================================================================
def test_mixed_plant_and_department_scopes_stay_a_union() -> None:
    """Plant-1 OR department-9, not plant-1 AND department-9.

    Flattening these into two sets and ANDing them would hide rows the caller is
    entitled to see; ORing the flattened sets would expose rows they are not.
    """
    principal = _principal(
        RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),
        RoleAssignment(Role.ANALYST, ScopeType.DEPARTMENT, DEPT_2),
    )
    scope = resolve_authorized_scope(principal, Permission.COST_READ)

    assert set(scope.branches) == {
        ScopeConstraint(tenant_id=TENANT_A, plant_id=PLANT_1),
        ScopeConstraint(tenant_id=TENANT_A, department_id=DEPT_2),
    }
    # Anything in plant-1.
    assert scope.covers(ResourceScope(TENANT_A, plant_id=PLANT_1, department_id=DEPT_1))
    # Department-2, wherever it lives.
    assert scope.covers(ResourceScope(TENANT_A, plant_id=PLANT_2, department_id=DEPT_2))
    # Neither.
    assert not scope.covers(ResourceScope(TENANT_A, plant_id=PLANT_2, department_id=DEPT_1))

    # The convenience properties correctly refuse to describe this shape.
    assert scope.plant_ids is None
    assert scope.department_ids is None


def test_tenant_wide_branch_absorbs_narrower_branches() -> None:
    principal = _principal(
        RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),
        RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),
    )
    scope = resolve_authorized_scope(principal, Permission.COST_READ)
    assert scope.branches == (ScopeConstraint(tenant_id=TENANT_A),)


def test_duplicate_assignments_do_not_duplicate_branches() -> None:
    principal = _principal(
        RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),
        RoleAssignment(Role.ANALYST, ScopeType.PLANT, PLANT_1),
    )
    scope = resolve_authorized_scope(principal, Permission.COST_READ)
    assert scope.branches == (ScopeConstraint(tenant_id=TENANT_A, plant_id=PLANT_1),)


# ===========================================================================
# Query filter and record check must agree
# ===========================================================================
@pytest.mark.parametrize(
    "assignments",
    [
        (RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
        (RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),),
        (RoleAssignment(Role.ANALYST, ScopeType.DEPARTMENT, DEPT_1),),
        (
            RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),
            RoleAssignment(Role.ANALYST, ScopeType.DEPARTMENT, DEPT_2),
        ),
    ],
)
def test_scope_filter_agrees_with_the_record_check(
    assignments: tuple[RoleAssignment, ...],
) -> None:
    """A row that survives the query filter must also pass the record check.

    These are two implementations of one rule. If they diverge, either rows leak
    past the filter or a caller is refused a record they were served.
    """
    principal = _principal(*assignments)
    scope = resolve_authorized_scope(principal, Permission.COST_READ)

    plants: list[str | None] = [PLANT_1, PLANT_2, None]
    departments: list[str | None] = [DEPT_1, DEPT_2, None]

    for tenant, plant, department in itertools.product(
        (TENANT_A, TENANT_B), plants, departments
    ):
        resource = ResourceScope(
            tenant_id=tenant, plant_id=plant, department_id=department
        )
        try:
            authorize_resource_permission(principal, resource, Permission.COST_READ)
        except (ForbiddenError, CrossTenantAccessError):
            record_allows = False
        else:
            record_allows = True

        assert scope.covers(resource) is record_allows, resource


# ===========================================================================
# AuthorizedScope invariants
# ===========================================================================
def test_an_empty_scope_cannot_be_constructed() -> None:
    """An empty disjunction reads as "no constraint" to a query builder, which
    is the opposite of what a denial means."""
    with pytest.raises(ValueError, match="at least one branch"):
        AuthorizedScope(tenant_id=TENANT_A, branches=())


def test_resource_scope_from_record_reads_the_ownership_columns() -> None:
    class Row:
        tenant_id = TENANT_A
        plant_id = PLANT_1
        department_id = DEPT_1

    assert ResourceScope.from_record(Row()) == ResourceScope(TENANT_A, PLANT_1, DEPT_1)


def test_resource_scope_from_record_tolerates_missing_optional_columns() -> None:
    class EnterpriseBudget:
        tenant_id = TENANT_A

    assert ResourceScope.from_record(EnterpriseBudget()) == ResourceScope(TENANT_A)


def test_resource_scope_from_record_refuses_a_row_with_no_tenant() -> None:
    """Guessing an owner is how a row becomes readable by the wrong tenant."""

    class Orphan:
        plant_id = PLANT_1

    with pytest.raises(ValueError, match="tenant_id"):
        ResourceScope.from_record(Orphan())


# ===========================================================================
# Over HTTP, through RequireScope
# ===========================================================================
async def test_scope_route_rejects_a_missing_token(client: AsyncClient) -> None:
    response = await client.get("/cost-scope")
    assert response.status_code == 401


async def test_scope_route_rejects_an_invalid_token(client: AsyncClient) -> None:
    response = await client.get("/cost-scope", headers=_auth("not-a-jwt"))
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


async def test_scope_route_rejects_an_expired_token(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(
        subject="pm-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),),
        issued_at=int(time.time()) - 3600,
        expires_in_seconds=60,
    )
    response = await client.get("/cost-scope", headers=_auth(token))
    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


async def test_scope_route_refuses_a_wrong_role(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """VIEWER does not hold AUDIT_READ, so no scope can be resolved for it."""
    token = adapter.issue_token(
        subject="viewer-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.get("/audit-scope", headers=_auth(token))
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_scope_route_refuses_a_plant_filter_outside_scope(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(
        subject="pm-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),),
    )
    response = await client.get(
        "/cost-scope", params={"plant_id": PLANT_2}, headers=_auth(token)
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_scope_route_returns_the_callers_constraint(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(
        subject="pm-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),),
    )
    response = await client.get("/cost-scope", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == TENANT_A
    assert body["is_tenant_wide"] is False
    assert body["branches"] == [
        {"tenant_id": TENANT_A, "plant_id": PLANT_1, "department_id": None}
    ]


async def test_scope_route_gives_an_admin_the_whole_tenant(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(
        subject="admin-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.get("/cost-scope", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert body["is_tenant_wide"] is True
    assert body["tenant_id"] == TENANT_A
