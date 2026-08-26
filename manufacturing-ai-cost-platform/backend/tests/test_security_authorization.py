"""Authorization tests: RBAC, tenant isolation, plant and department scope.

Covers the required cases: wrong role, cross-tenant access, and authorized
access, at both the endpoint and the resource level.

Every negative assertion here is a security boundary. If one fails, fix the
authorization code — never the expectation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.errors import ForbiddenError
from app.security.authorization import (
    CrossTenantAccessError,
    authorize_resource,
    can_access_resource,
    reject_client_tenant_override,
)
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import (
    Principal,
    ResourceScope,
    Role,
    RoleAssignment,
    ScopeType,
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
# Endpoint-level RBAC
# ===========================================================================
async def test_wrong_role_is_forbidden(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """A VIEWER may authenticate but must not reach a FinOps endpoint."""
    token = adapter.issue_token(
        subject="viewer-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.get("/finops-only", headers=_auth(token))

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_correct_role_is_allowed(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(
        subject="finops-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.FINOPS_MANAGER, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.get("/finops-only", headers=_auth(token))
    assert response.status_code == 200


async def test_role_check_runs_after_authentication(client: AsyncClient) -> None:
    """An unauthenticated call to a role-guarded route is 401, not 403."""
    response = await client.get("/finops-only")
    assert response.status_code == 401


async def test_no_role_at_all_is_forbidden(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(subject="nobody", tenant_id=TENANT_A, assignments=())
    response = await client.get("/finops-only", headers=_auth(token))
    assert response.status_code == 403


# ===========================================================================
# Tenant isolation
# ===========================================================================
async def test_cross_tenant_resource_access_is_refused(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """A tenant-A admin must not read a tenant-B budget.

    Reported as 404: a 403 would confirm the record exists, which is itself a
    disclosure across the tenant boundary.
    """
    token = adapter.issue_token(
        subject="admin-a",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.get(
        "/budgets/budget-9",
        params={"tenant_id": TENANT_B},
        headers=_auth(token),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_client_supplied_tenant_override_is_refused(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """SECURITY.md section 5: never trust a client-provided tenant id."""
    token = adapter.issue_token(
        subject="admin-a",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.get(
        "/cost-summary", params={"tenant_id": TENANT_B}, headers=_auth(token)
    )
    assert response.status_code == 404


async def test_tenant_is_derived_from_the_token_when_client_sends_none(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(
        subject="admin-a",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.get("/cost-summary", headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["tenant_id"] == TENANT_A


async def test_echoing_own_tenant_is_permitted(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(
        subject="admin-a",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.get(
        "/cost-summary", params={"tenant_id": TENANT_A}, headers=_auth(token)
    )
    assert response.status_code == 200


async def test_token_claiming_tenant_scope_over_another_tenant_is_refused(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """A forged-looking assignment must not cross the tenant boundary.

    The token is validly signed but asserts TENANT scope over tenant-B while its
    tenant claim is tenant-A. Tenant is checked against the principal's own
    tenant, so the assignment cannot reach across.
    """
    token = adapter.issue_token(
        subject="admin-a",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_B),),
    )
    response = await client.get(
        "/budgets/budget-9", params={"tenant_id": TENANT_B}, headers=_auth(token)
    )
    assert response.status_code == 404


# ===========================================================================
# Plant scope — the SECURITY.md section 4 example
# ===========================================================================
async def test_plant_manager_cannot_read_another_plants_budget(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """"A Plant Manager should not automatically see another plant's budgets"."""
    token = adapter.issue_token(
        subject="pm-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),),
    )
    response = await client.get(
        "/budgets/budget-2",
        params={"tenant_id": TENANT_A, "plant_id": PLANT_2},
        headers=_auth(token),
    )

    # Same tenant, correct role, wrong plant: 403, not 404.
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_plant_manager_can_read_their_own_plants_budget(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(
        subject="pm-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),),
    )
    response = await client.get(
        "/budgets/budget-1",
        params={"tenant_id": TENANT_A, "plant_id": PLANT_1},
        headers=_auth(token),
    )
    assert response.status_code == 200


async def test_plant_scoped_role_cannot_read_a_tenant_wide_resource(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """An enterprise-wide budget has no plant, so plant scope does not cover it."""
    token = adapter.issue_token(
        subject="pm-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),),
    )
    response = await client.get(
        "/budgets/enterprise-budget",
        params={"tenant_id": TENANT_A},
        headers=_auth(token),
    )
    assert response.status_code == 403


async def test_tenant_scoped_role_covers_every_plant(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(
        subject="admin-a",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )
    for plant in (PLANT_1, PLANT_2):
        response = await client.get(
            "/budgets/b",
            params={"tenant_id": TENANT_A, "plant_id": plant},
            headers=_auth(token),
        )
        assert response.status_code == 200, plant


# ===========================================================================
# Unit-level scope rules
# ===========================================================================
def test_department_scope_grants_only_that_department() -> None:
    principal = _principal(
        RoleAssignment(Role.ANALYST, ScopeType.DEPARTMENT, DEPT_1)
    )

    authorize_resource(
        principal,
        ResourceScope(tenant_id=TENANT_A, plant_id=PLANT_1, department_id=DEPT_1),
        Role.ANALYST,
    )

    with pytest.raises(ForbiddenError):
        authorize_resource(
            principal,
            ResourceScope(tenant_id=TENANT_A, plant_id=PLANT_1, department_id=DEPT_2),
            Role.ANALYST,
        )


def test_department_scope_does_not_grant_the_whole_plant() -> None:
    principal = _principal(
        RoleAssignment(Role.ANALYST, ScopeType.DEPARTMENT, DEPT_1)
    )
    with pytest.raises(ForbiddenError):
        authorize_resource(
            principal,
            ResourceScope(tenant_id=TENANT_A, plant_id=PLANT_1),
            Role.ANALYST,
        )


def test_holding_a_role_elsewhere_does_not_grant_it_here() -> None:
    """Role and scope are checked together, not independently."""
    principal = _principal(
        RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),
        RoleAssignment(Role.VIEWER, ScopeType.PLANT, PLANT_2),
    )
    # PLANT_MANAGER is held, but at plant-1, not plant-2.
    with pytest.raises(ForbiddenError):
        authorize_resource(
            principal,
            ResourceScope(tenant_id=TENANT_A, plant_id=PLANT_2),
            Role.PLANT_MANAGER,
        )


def test_tenant_check_precedes_the_role_check() -> None:
    """Cross-tenant returns 404 even when the role would be insufficient.

    Otherwise the status code would reveal whether the role was the problem,
    which distinguishes an existing record from a missing one.
    """
    principal = _principal(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A))
    with pytest.raises(CrossTenantAccessError):
        authorize_resource(
            principal,
            ResourceScope(tenant_id=TENANT_B, plant_id=PLANT_1),
            Role.ADMIN,
        )


def test_can_access_resource_is_a_non_raising_mirror() -> None:
    principal = _principal(
        RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1)
    )
    assert can_access_resource(
        principal, ResourceScope(TENANT_A, plant_id=PLANT_1), Role.PLANT_MANAGER
    )
    assert not can_access_resource(
        principal, ResourceScope(TENANT_A, plant_id=PLANT_2), Role.PLANT_MANAGER
    )
    assert not can_access_resource(
        principal, ResourceScope(TENANT_B, plant_id=PLANT_1), Role.PLANT_MANAGER
    )


def test_authorize_resource_requires_at_least_one_role() -> None:
    """A guard called with no roles is a bug, not an open door."""
    principal = _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A))
    with pytest.raises(ValueError, match="[Aa]t least one role"):
        authorize_resource(principal, ResourceScope(TENANT_A))


def test_reject_client_tenant_override_returns_the_authenticated_tenant() -> None:
    principal = _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A))
    assert reject_client_tenant_override(principal, None) == TENANT_A
    assert reject_client_tenant_override(principal, TENANT_A) == TENANT_A
    with pytest.raises(CrossTenantAccessError):
        reject_client_tenant_override(principal, TENANT_B)


# ===========================================================================
# Query-constraining helpers
# ===========================================================================
def test_scope_ids_expose_only_the_caller_s_plants() -> None:
    principal = _principal(
        RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),
        RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_2),
        RoleAssignment(Role.VIEWER, ScopeType.PLANT, "plant-3"),
    )
    assert principal.scope_ids(Role.PLANT_MANAGER, ScopeType.PLANT) == {PLANT_1, PLANT_2}
    assert principal.accessible_plant_ids(Role.PLANT_MANAGER) == {PLANT_1, PLANT_2}
    assert not principal.has_tenant_wide_access(Role.PLANT_MANAGER)


def test_tenant_wide_access_is_detected() -> None:
    principal = _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A))
    assert principal.has_tenant_wide_access(Role.ADMIN)
    # Empty plant set does not mean "no plants" when tenant-wide access is held.
    assert principal.accessible_plant_ids(Role.ADMIN) == frozenset()


def test_tenant_wide_access_requires_the_principals_own_tenant() -> None:
    principal = _principal(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_B))
    assert not principal.has_tenant_wide_access(Role.ADMIN)
