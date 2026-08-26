"""RBAC: permissions and the role grant table.

SECURITY.md section 4 names six roles and requires authorization at endpoint and
resource level. It does **not** state which operation each role may perform, and
neither does API_CONTRACT.yaml — every secured operation declares only
``bearerAuth``.

AI_DEVELOPMENT_RULES.md section 3 covers exactly this case: where the contract
is silent, create a documented configuration point rather than guessing in
scattered call sites. ``ROLE_PERMISSIONS`` below is that configuration point.

    ASSUMPTION. The grants in ROLE_PERMISSIONS are a default derived from the
    role names in SECURITY.md section 4, the operations declared in
    API_CONTRACT.yaml, and the one worked example that document gives (a plant
    manager must not automatically see another plant's budgets). They are not
    quoted from a source-of-truth document. When the intended matrix is
    established, correct it here — and only here. No call site restates it.

Why permissions rather than roles at each call site:

* A route naming ``Role.ADMIN, Role.FINOPS_MANAGER, Role.PLANT_MANAGER`` states
  policy in a place nobody audits. Thirty routes state it thirty times, and they
  drift.
* Adding a role then means editing every route that should include it. Missing
  one is silent over- or under-permissioning.
* A permission names *what the caller is doing*, which is what a resource-level
  check also needs — so the endpoint and resource checks stay in agreement.

Deny by default. A permission absent from a role's set is refused; there is no
wildcard and no implicit inheritance between roles. ADMIN is granted every
permission explicitly, so a newly added permission shows up in this table
immediately rather than being silently granted somewhere.

Holding a permission is never sufficient on its own. It answers "may this kind
of caller do this at all?" — the endpoint-level half of SECURITY.md section 4.
The resource-level half (which tenant, plant and department) is enforced in
``app.security.authorization`` and ``app.security.scope``.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.errors import ForbiddenError
from app.security.events import SecurityEvent, record_security_event
from app.security.principal import Principal, Role, RoleAssignment


class Permission(StrEnum):
    """One authorizable operation.

    Each value maps to operations declared in API_CONTRACT.yaml. Permissions are
    not invented for endpoints the contract does not define.
    """

    # POST /ai/execute
    AI_EXECUTE = "ai:execute"

    # GET /cost/summary, /cost/by-model, /cost/by-agent, /cost/by-plant, /cost/trend
    COST_READ = "cost:read"

    # GET /budgets, GET /budgets/status
    BUDGET_READ = "budget:read"
    # POST /budgets, PATCH /budgets/{id}
    BUDGET_MANAGE = "budget:manage"

    # GET /forecasts
    FORECAST_READ = "forecast:read"

    # GET /anomalies
    ANOMALY_READ = "anomaly:read"

    # GET /optimization/recommendations
    OPTIMIZATION_READ = "optimization:read"
    # POST /optimization/analyze
    OPTIMIZATION_ANALYZE = "optimization:analyze"
    # POST /optimization/{id}/approve
    OPTIMIZATION_APPROVE = "optimization:approve"
    # POST /optimization/{id}/apply, POST /optimization/{id}/rollback
    OPTIMIZATION_APPLY = "optimization:apply"

    # GET /models
    MODEL_READ = "model:read"
    # PATCH /models/{id}
    MODEL_MANAGE = "model:manage"

    # GET /workloads, GET /agents
    WORKLOAD_READ = "workload:read"

    # GET /plants, GET /departments
    ORGANIZATION_READ = "organization:read"

    # GET /policies
    POLICY_READ = "policy:read"

    # GET /governance/approvals
    APPROVAL_READ = "approval:read"
    # POST /governance/approvals/{id}/decide
    APPROVAL_DECIDE = "approval:decide"

    # GET /governance/audit
    AUDIT_READ = "audit:read"


# Read-only visibility, shared by every role. Scope still limits *which* records
# are visible, which is where a VIEWER differs from an ADMIN in practice.
_READ_ONLY: frozenset[Permission] = frozenset(
    {
        Permission.COST_READ,
        Permission.BUDGET_READ,
        Permission.FORECAST_READ,
        Permission.ANOMALY_READ,
        Permission.OPTIMIZATION_READ,
        Permission.MODEL_READ,
        Permission.WORKLOAD_READ,
        Permission.ORGANIZATION_READ,
        Permission.POLICY_READ,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    # Break-glass administration. Deliberately holds both OPTIMIZATION_APPROVE
    # and OPTIMIZATION_APPLY, the one place the separation of duties below is
    # not maintained; ADMIN assignments should stay rare and audited.
    Role.ADMIN: frozenset(Permission),
    # Owns budgets and money. Approves optimizations on cost grounds and reads
    # the audit trail; does not apply a policy to production itself.
    Role.FINOPS_MANAGER: _READ_ONLY
    | {
        Permission.BUDGET_MANAGE,
        Permission.OPTIMIZATION_ANALYZE,
        Permission.OPTIMIZATION_APPROVE,
        Permission.APPROVAL_READ,
        Permission.APPROVAL_DECIDE,
        Permission.AUDIT_READ,
    },
    # Owns models and execution. Applies an approved policy but cannot approve
    # one: SECURITY.md section 15 requires approval to precede activation, and a
    # single role holding both collapses that control.
    Role.AI_ENGINEER: _READ_ONLY
    | {
        Permission.AI_EXECUTE,
        Permission.MODEL_MANAGE,
        Permission.OPTIMIZATION_ANALYZE,
        Permission.OPTIMIZATION_APPLY,
        Permission.APPROVAL_READ,
    },
    # Runs a plant's workloads and decides approvals raised within their scope.
    # Their PLANT-scoped assignment is what stops that reaching another plant.
    Role.PLANT_MANAGER: _READ_ONLY
    | {
        Permission.AI_EXECUTE,
        Permission.APPROVAL_READ,
        Permission.APPROVAL_DECIDE,
    },
    # Investigates cost and quality. May trigger an optimization analysis, which
    # produces a recommendation only — never a production change.
    Role.ANALYST: _READ_ONLY
    | {
        Permission.OPTIMIZATION_ANALYZE,
        Permission.APPROVAL_READ,
    },
    # Read-only. No approval, no execution, no configuration.
    Role.VIEWER: _READ_ONLY,
}


def permissions_for(*roles: Role) -> frozenset[Permission]:
    """Union of the permissions granted by ``roles``.

    An unknown role contributes nothing rather than raising: the claims mapper
    already drops role names it does not recognise, and a lookup miss must never
    widen access.
    """
    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(granted)


def roles_granting(permission: Permission) -> frozenset[Role]:
    """Roles that grant ``permission``. Used for diagnostics and tests."""
    return frozenset(
        role for role, granted in ROLE_PERMISSIONS.items() if permission in granted
    )


def principal_permissions(principal: Principal) -> frozenset[Permission]:
    """Every permission the caller holds at any scope."""
    return permissions_for(*principal.roles)


def has_permission(principal: Principal, permission: Permission) -> bool:
    """Endpoint-level test, non-raising."""
    return permission in principal_permissions(principal)


def assignments_granting(
    principal: Principal, permission: Permission
) -> tuple[RoleAssignment, ...]:
    """Assignments whose role grants ``permission``.

    This is the bridge to resource-level authorization: the scope attached to
    *these* assignments — not to every assignment the caller holds — is what may
    authorize a record. A VIEWER assignment on plant-2 must not help a
    BUDGET_MANAGE request reach plant-2.
    """
    return tuple(
        assignment
        for assignment in principal.assignments
        if permission in ROLE_PERMISSIONS.get(assignment.role, frozenset())
    )


def require_permission(principal: Principal, permission: Permission) -> None:
    """Endpoint-level guard.

    Raises:
        ForbiddenError: if no role the caller holds grants ``permission``.
    """
    if not has_permission(principal, permission):
        record_security_event(
            SecurityEvent.AUTHORIZATION_DENIED,
            reason="permission_not_granted",
            required_permission=str(permission),
            held_roles=sorted(str(role) for role in principal.roles),
            check_level="endpoint",
        )
        raise ForbiddenError()
