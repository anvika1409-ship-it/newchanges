"""RBAC tests: the permission grant table and the endpoint-level guard.

Covers the required cases at the permission layer: unauthenticated request,
invalid token, expired token, wrong role, cross-tenant access and authorized
access.

Every negative assertion here is a security boundary. If one fails, fix the
authorization code — never the expectation.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.errors import ForbiddenError
from app.security.authorization import (
    CrossTenantAccessError,
    authorize_resource_permission,
    can_access_resource_permission,
)
from app.security.identity import DevelopmentIdentityAdapter
from app.security.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    assignments_granting,
    has_permission,
    permissions_for,
    principal_permissions,
    require_permission,
    roles_granting,
)
from app.security.principal import (
    Principal,
    ResourceScope,
    Role,
    RoleAssignment,
    ScopeType,
)
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER
from tests.security_app import create_security_test_app

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
PLANT_1 = "plant-1"
PLANT_2 = "plant-2"


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


def _token(
    adapter: DevelopmentIdentityAdapter,
    role: Role,
    *,
    scope_type: ScopeType = ScopeType.TENANT,
    scope_id: str | None = None,
    tenant_id: str = TENANT_A,
    **kwargs: object,
) -> str:
    return adapter.issue_token(
        subject=f"user-{role.lower()}",
        tenant_id=tenant_id,
        assignments=(
            RoleAssignment(role, scope_type, scope_id or tenant_id),
        ),
        **kwargs,  # type: ignore[arg-type]
    )


# ===========================================================================
# Grant table properties
# ===========================================================================
def test_every_role_has_an_explicit_grant_set() -> None:
    """A role missing from the table would silently hold nothing — or, if a
    lookup ever defaulted the other way, everything. Neither is acceptable."""
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_admin_holds_every_permission() -> None:
    """A newly added permission must appear in the table, not be granted by a
    wildcard nobody reviews."""
    assert ROLE_PERMISSIONS[Role.ADMIN] == frozenset(Permission)


def test_viewer_holds_no_mutating_permission() -> None:
    mutating = {
        Permission.AI_EXECUTE,
        Permission.BUDGET_MANAGE,
        Permission.MODEL_MANAGE,
        Permission.OPTIMIZATION_ANALYZE,
        Permission.OPTIMIZATION_APPROVE,
        Permission.OPTIMIZATION_APPLY,
        Permission.APPROVAL_DECIDE,
        Permission.AUDIT_READ,
    }
    assert not ROLE_PERMISSIONS[Role.VIEWER] & mutating


def test_approval_and_application_are_separated_below_admin() -> None:
    """SECURITY.md section 15 puts approval before activation. A non-admin role
    holding both collapses that control into one signature."""
    for role, granted in ROLE_PERMISSIONS.items():
        if role is Role.ADMIN:
            continue
        holds_both = (
            Permission.OPTIMIZATION_APPROVE in granted
            and Permission.OPTIMIZATION_APPLY in granted
        )
        assert not holds_both, role


def test_every_permission_is_granted_to_someone() -> None:
    """An ungrantable permission is a locked-out endpoint waiting to happen."""
    for permission in Permission:
        assert roles_granting(permission), permission


def test_unknown_role_grants_nothing() -> None:
    """A lookup miss must never widen access."""
    assert permissions_for() == frozenset()


def test_permissions_for_is_a_union() -> None:
    combined = permissions_for(Role.VIEWER, Role.FINOPS_MANAGER)
    assert combined == ROLE_PERMISSIONS[Role.VIEWER] | ROLE_PERMISSIONS[Role.FINOPS_MANAGER]


# ===========================================================================
# require_permission — endpoint level, unit
# ===========================================================================
def test_require_permission_allows_a_granting_role() -> None:
    principal = _principal(RoleAssignment(Role.FINOPS_MANAGER, ScopeType.TENANT, TENANT_A))
    require_permission(principal, Permission.BUDGET_MANAGE)


def test_require_permission_refuses_a_role_without_the_grant() -> None:
    principal = _principal(RoleAssignment(Role.ANALYST, ScopeType.TENANT, TENANT_A))
    with pytest.raises(ForbiddenError):
        require_permission(principal, Permission.BUDGET_MANAGE)


def test_require_permission_refuses_a_principal_with_no_roles() -> None:
    principal = _principal()
    assert principal_permissions(principal) == frozenset()
    with pytest.raises(ForbiddenError):
        require_permission(principal, Permission.COST_READ)


def test_has_permission_mirrors_require_permission() -> None:
    principal = _principal(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT_A))
    assert has_permission(principal, Permission.MODEL_MANAGE)
    assert not has_permission(principal, Permission.OPTIMIZATION_APPROVE)


# ===========================================================================
# assignments_granting — the bridge to resource-level scope
# ===========================================================================
def test_only_assignments_whose_role_grants_the_permission_contribute_scope() -> None:
    """A read-only assignment on another plant must not extend a write there."""
    principal = _principal(
        RoleAssignment(Role.FINOPS_MANAGER, ScopeType.PLANT, PLANT_1),
        RoleAssignment(Role.VIEWER, ScopeType.PLANT, PLANT_2),
    )

    manage = assignments_granting(principal, Permission.BUDGET_MANAGE)
    assert manage == (RoleAssignment(Role.FINOPS_MANAGER, ScopeType.PLANT, PLANT_1),)

    # Both roles can read, so both scopes count for a read.
    read = assignments_granting(principal, Permission.BUDGET_READ)
    assert len(read) == 2


def test_assignments_granting_is_empty_when_no_role_grants_it() -> None:
    principal = _principal(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A))
    assert assignments_granting(principal, Permission.AUDIT_READ) == ()


# ===========================================================================
# Endpoint level over HTTP
# ===========================================================================
# ------------------------------------------------------- unauthenticated
async def test_permission_guarded_route_rejects_a_missing_token(
    client: AsyncClient,
) -> None:
    """No token is 401, not 403: authentication is evaluated first."""
    response = await client.get("/budget-manage")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


# -------------------------------------------------------- invalid token
async def test_permission_guarded_route_rejects_an_invalid_token(
    client: AsyncClient,
) -> None:
    response = await client.get("/budget-manage", headers=_auth("not-a-jwt"))
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


async def test_permission_guarded_route_rejects_a_forged_signature(
    client: AsyncClient,
) -> None:
    now = int(time.time())
    forged = jwt.encode(
        {
            "sub": "attacker",
            "iat": now,
            "exp": now + 900,
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "tenant_id": TENANT_A,
            "roles": ["ADMIN"],
        },
        "a-different-signing-key-padded-to-thirty-two-bytes",
        algorithm="HS256",
    )
    response = await client.get("/budget-manage", headers=_auth(forged))
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


# --------------------------------------------------------- expired token
async def test_permission_guarded_route_rejects_an_expired_token(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = _token(
        adapter,
        Role.ADMIN,
        issued_at=int(time.time()) - 3600,
        expires_in_seconds=60,
    )
    response = await client.get("/budget-manage", headers=_auth(token))
    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


# ------------------------------------------------------------- wrong role
async def test_analyst_is_refused_budget_management(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    response = await client.get(
        "/budget-manage", headers=_auth(_token(adapter, Role.ANALYST))
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_viewer_is_refused_every_managing_permission(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = _token(adapter, Role.VIEWER)
    for path in ("/budget-manage", "/model-manage", "/audit-scope"):
        response = await client.get(path, headers=_auth(token))
        assert response.status_code == 403, path


async def test_finops_manager_may_manage_budgets_but_not_models(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """Two permissions, two outcomes, from one token."""
    token = _token(adapter, Role.FINOPS_MANAGER)
    assert (await client.get("/budget-manage", headers=_auth(token))).status_code == 200
    assert (await client.get("/model-manage", headers=_auth(token))).status_code == 403


async def test_ai_engineer_may_manage_models_but_not_budgets(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = _token(adapter, Role.AI_ENGINEER)
    assert (await client.get("/model-manage", headers=_auth(token))).status_code == 200
    assert (await client.get("/budget-manage", headers=_auth(token))).status_code == 403


# ----------------------------------------------------- cross-tenant access
async def test_permission_does_not_reach_across_tenants(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """An ADMIN of tenant-a is still nobody in tenant-b.

    Reported as 404: a 403 would confirm the record exists.
    """
    token = _token(adapter, Role.ADMIN)
    response = await client.get(
        "/budgets-by-permission/budget-9",
        params={"tenant_id": TENANT_B},
        headers=_auth(token),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_resource_permission_checks_tenant_before_permission() -> None:
    """Cross-tenant is 404 even when the permission would also have failed,
    so the status code cannot be used to probe for a record's existence."""
    principal = _principal(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A))
    with pytest.raises(CrossTenantAccessError):
        authorize_resource_permission(
            principal,
            ResourceScope(tenant_id=TENANT_B, plant_id=PLANT_1),
            Permission.BUDGET_MANAGE,
        )


def test_can_access_resource_permission_is_a_non_raising_mirror() -> None:
    principal = _principal(
        RoleAssignment(Role.FINOPS_MANAGER, ScopeType.PLANT, PLANT_1),
        RoleAssignment(Role.VIEWER, ScopeType.PLANT, PLANT_2),
    )
    assert can_access_resource_permission(
        principal, ResourceScope(TENANT_A, plant_id=PLANT_1), Permission.BUDGET_MANAGE
    )
    # Right tenant and plant, but only a read-only role there.
    assert not can_access_resource_permission(
        principal, ResourceScope(TENANT_A, plant_id=PLANT_2), Permission.BUDGET_MANAGE
    )
    # Another tenant, refused rather than raising.
    assert not can_access_resource_permission(
        principal, ResourceScope(TENANT_B, plant_id=PLANT_1), Permission.BUDGET_MANAGE
    )


# ------------------------------------------------------ resource-level scope
async def test_plant_manager_cannot_read_another_plants_budget_by_permission(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """The SECURITY.md section 4 example, enforced through the permission path."""
    token = _token(
        adapter, Role.PLANT_MANAGER, scope_type=ScopeType.PLANT, scope_id=PLANT_1
    )
    response = await client.get(
        "/budgets-by-permission/budget-2",
        params={"tenant_id": TENANT_A, "plant_id": PLANT_2},
        headers=_auth(token),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_read_only_scope_elsewhere_does_not_authorize_a_write(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """FINOPS_MANAGER on plant-1, VIEWER on plant-2.

    The caller passes the endpoint guard because they can manage budgets
    somewhere. The write must still be refused on plant-2, where they only hold
    a read-only role.
    """
    token = adapter.issue_token(
        subject="mixed-1",
        tenant_id=TENANT_A,
        assignments=(
            RoleAssignment(Role.FINOPS_MANAGER, ScopeType.PLANT, PLANT_1),
            RoleAssignment(Role.VIEWER, ScopeType.PLANT, PLANT_2),
        ),
    )

    allowed = await client.get(
        "/budget-write/b1",
        params={"tenant_id": TENANT_A, "plant_id": PLANT_1},
        headers=_auth(token),
    )
    assert allowed.status_code == 200

    refused = await client.get(
        "/budget-write/b2",
        params={"tenant_id": TENANT_A, "plant_id": PLANT_2},
        headers=_auth(token),
    )
    assert refused.status_code == 403

    # ...while a read on plant-2 is fine, because VIEWER grants BUDGET_READ.
    readable = await client.get(
        "/budgets-by-permission/b2",
        params={"tenant_id": TENANT_A, "plant_id": PLANT_2},
        headers=_auth(token),
    )
    assert readable.status_code == 200


# ---------------------------------------------------------- authorized access
async def test_authorized_access_succeeds(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = _token(adapter, Role.FINOPS_MANAGER)
    response = await client.get(
        "/budgets-by-permission/budget-1",
        params={"tenant_id": TENANT_A, "plant_id": PLANT_1},
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert response.json() == {"budget_id": "budget-1", "tenant_id": TENANT_A}
