"""Authorization.

SECURITY.md section 4 requires authorization at two levels:

1. **Endpoint level** — does the caller hold a role that may call this operation?
2. **Resource level** — may this caller touch *this particular* record?

Both are needed. A PLANT_MANAGER may legitimately call the budgets endpoint and
still be forbidden from reading another plant's budget. Endpoint checks alone
would let that through.

Scope hierarchy:

    TENANT      -> every plant and department in the tenant
    PLANT       -> that plant, and departments within it
    DEPARTMENT  -> that department only

Cross-tenant access is reported as 404, not 403. A 403 confirms the record
exists, which is itself a disclosure across a tenant boundary. This matches the
``NotFound`` description in API_CONTRACT.yaml: "Resource not found or not
visible to the caller".
"""

from __future__ import annotations

from app.core.errors import ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.security.principal import Principal, ResourceScope, Role, RoleAssignment, ScopeType

logger = get_logger(__name__)


class CrossTenantAccessError(NotFoundError):
    """Raised when a caller reaches for another tenant's data.

    Deliberately a 404. See the module docstring.
    """

    code = "not_found"
    message = "Resource not found or not visible to the caller"


def require_roles(principal: Principal, *roles: Role) -> None:
    """Endpoint-level guard.

    Raises:
        ForbiddenError: if the caller holds none of ``roles``.
    """
    if not roles:
        raise ValueError("At least one role must be required")

    if not principal.has_role(*roles):
        logger.warning(
            "authorization_denied_endpoint",
            extra={
                "required_roles": sorted(str(r) for r in roles),
                "held_roles": sorted(str(r) for r in principal.roles),
            },
        )
        raise ForbiddenError()


def require_tenant(principal: Principal, tenant_id: str) -> None:
    """Reject any access outside the authenticated tenant.

    Called with a tenant id that came from a stored record, never from the
    request body. A client-supplied tenant is checked by
    ``reject_client_tenant_override``.
    """
    if tenant_id != principal.tenant_id:
        logger.warning("authorization_denied_cross_tenant")
        raise CrossTenantAccessError()


def reject_client_tenant_override(principal: Principal, claimed_tenant_id: str | None) -> str:
    """Resolve the effective tenant, refusing a client-supplied mismatch.

    SECURITY.md section 5: never trust a client-provided tenant id. A caller may
    echo their own tenant, but anything else is refused rather than silently
    ignored — silently ignoring it hides a probe that should be visible.

    Returns:
        The authenticated tenant id, which is the only one queries may use.
    """
    if claimed_tenant_id is not None and claimed_tenant_id != principal.tenant_id:
        logger.warning("authorization_denied_tenant_override")
        raise CrossTenantAccessError()
    return principal.tenant_id


def _assignment_covers(assignment: RoleAssignment, resource: ResourceScope) -> bool:
    """Does this assignment's scope contain the resource?"""
    match assignment.scope_type:
        case ScopeType.TENANT:
            # Guards against a token asserting TENANT scope over another tenant.
            return assignment.scope_id == resource.tenant_id
        case ScopeType.PLANT:
            return resource.plant_id is not None and assignment.scope_id == resource.plant_id
        case ScopeType.DEPARTMENT:
            return (
                resource.department_id is not None
                and assignment.scope_id == resource.department_id
            )
    return False


def authorize_resource(
    principal: Principal,
    resource: ResourceScope,
    *roles: Role,
) -> None:
    """Full check: tenant, role, then scope.

    Order matters. Tenant is evaluated first so a cross-tenant probe returns 404
    without revealing whether the caller's role would have sufficed.

    Raises:
        CrossTenantAccessError: resource belongs to another tenant (404).
        ForbiddenError: right tenant, but wrong role or out of scope (403).
    """
    require_tenant(principal, resource.tenant_id)

    if not roles:
        raise ValueError("At least one role must be required")

    candidates = principal.assignments_for(*roles)
    if not candidates:
        logger.warning(
            "authorization_denied_resource_role",
            extra={"required_roles": sorted(str(r) for r in roles)},
        )
        raise ForbiddenError()

    if not any(_assignment_covers(a, resource) for a in candidates):
        # The caller holds the role, but not over this plant or department.
        # This is the SECURITY.md section 4 example.
        logger.warning(
            "authorization_denied_resource_scope",
            extra={
                "required_roles": sorted(str(r) for r in roles),
                "resource_plant_id": resource.plant_id,
                "resource_department_id": resource.department_id,
            },
        )
        raise ForbiddenError()


def can_access_resource(principal: Principal, resource: ResourceScope, *roles: Role) -> bool:
    """Non-raising form, for filtering collections.

    Prefer constraining the query with ``Principal.scope_ids`` where possible;
    fetching rows only to discard them wastes work and risks leaking counts.
    """
    try:
        authorize_resource(principal, resource, *roles)
    except (ForbiddenError, CrossTenantAccessError):
        return False
    return True
