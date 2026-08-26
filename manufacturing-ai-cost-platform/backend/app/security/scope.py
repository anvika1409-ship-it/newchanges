"""Tenant, plant and department scope resolution.

``app.security.authorization`` answers "may this caller touch *this record*?".
That works when a record has already been loaded. It does not work for a
collection query — ``GET /cost/summary``, ``GET /budgets`` — where loading
everything and discarding the rows the caller may not see would leak counts,
waste work, and break pagination totals.

This module produces the other half: an ``AuthorizedScope`` that a repository
turns into a WHERE clause, so unauthorized rows are never read in the first
place (SECURITY.md section 5: "Queries must include the tenant scope derived
from authenticated identity").

Shape of the constraint
-----------------------

A caller's reach is a *union* of the scopes their role assignments cover. A user
may hold PLANT_MANAGER on plant-1 and ANALYST on department-9 in another plant;
neither a single plant filter nor a single department filter can express that.
So an ``AuthorizedScope`` is a disjunction of ``ScopeConstraint`` branches, each
of which is a conjunction:

    tenant = T AND (
        (plant = plant-1)
        OR (department = department-9)
    )

The tenant is factored out because it is never optional.

Client-supplied filters narrow, never widen
-------------------------------------------

``plant_id`` and ``department_id`` are declared query parameters in
API_CONTRACT.yaml. They are treated as *requests to narrow*. A filter that no
branch can satisfy is refused with 403 rather than silently ignored — silently
ignoring it would return the caller's own data in response to a probe for
someone else's, which reads as success and hides the attempt.

A department filter from a plant-scoped caller is allowed and narrows within
that caller's plants: the branch keeps its plant constraint, so a department
belonging to another plant simply matches nothing. The parent lookup that would
otherwise be needed to reject it up front requires the ``departments`` table,
which does not exist yet; the conjunctive filter is safe without it.

Consistency with record-level checks
------------------------------------

``AuthorizedScope.covers`` uses the same scope semantics as
``authorization._assignment_covers``. The two must agree — a row that survives
the query filter must also pass the record check, and vice versa — and
``tests/test_security_scope.py`` asserts that on every combination.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import ForbiddenError
from app.security.authorization import require_tenant
from app.security.events import SecurityEvent, record_security_event
from app.security.permissions import Permission, assignments_granting
from app.security.principal import Principal, ResourceScope, ScopeType


@dataclass(frozen=True, slots=True)
class ScopeConstraint:
    """One branch of an authorized scope: tenant AND plant AND department.

    ``None`` means *unconstrained at that level*, not "must be null":

    * ``plant_id=None, department_id=None`` — the whole tenant.
    * ``plant_id="plant-1", department_id=None`` — plant-1 and everything in it.
    * ``plant_id=None, department_id="dept-9"`` — that department only.
    """

    tenant_id: str
    plant_id: str | None = None
    department_id: str | None = None

    def covers(self, resource: ResourceScope) -> bool:
        """Would a record with this ownership satisfy the branch?

        Each level is checked only when this branch constrains it, so a
        plant-only branch covers every department inside that plant — matching
        ``authorization._assignment_covers`` for a PLANT assignment.
        """
        return (
            resource.tenant_id == self.tenant_id
            and (self.plant_id is None or resource.plant_id == self.plant_id)
            and (
                self.department_id is None
                or resource.department_id == self.department_id
            )
        )

    @property
    def is_tenant_wide(self) -> bool:
        return self.plant_id is None and self.department_id is None


@dataclass(frozen=True, slots=True)
class AuthorizedScope:
    """The rows a caller may see, as a disjunction of constraints.

    ``branches`` is never empty: an empty disjunction would mean "no constraint"
    to a careless query builder, which is the opposite of what it represents.
    Resolution raises ``ForbiddenError`` instead of returning one.
    """

    tenant_id: str
    branches: tuple[ScopeConstraint, ...]

    def __post_init__(self) -> None:
        if not self.branches:
            raise ValueError(
                "An AuthorizedScope must carry at least one branch. Denial is "
                "raised as ForbiddenError, never encoded as an empty scope."
            )

    @property
    def is_tenant_wide(self) -> bool:
        """True when the caller may see every record in the tenant."""
        return any(branch.is_tenant_wide for branch in self.branches)

    @property
    def plant_ids(self) -> frozenset[str] | None:
        """Plants to filter on, or ``None`` when plants are unconstrained.

        Convenience for the common single-level query. A repository handling the
        mixed case must read ``branches`` — collapsing a union of plant and
        department branches into two flat sets and ANDing them would be wrong.
        """
        if any(branch.plant_id is None for branch in self.branches):
            return None
        return frozenset(
            branch.plant_id for branch in self.branches if branch.plant_id is not None
        )

    @property
    def department_ids(self) -> frozenset[str] | None:
        """Departments to filter on, or ``None`` when unconstrained."""
        if any(branch.department_id is None for branch in self.branches):
            return None
        return frozenset(
            branch.department_id
            for branch in self.branches
            if branch.department_id is not None
        )

    def covers(self, resource: ResourceScope) -> bool:
        """Would a row with this ownership survive the filter?"""
        return any(branch.covers(resource) for branch in self.branches)

    # ------------------------------------------------------------- narrowing
    def narrowed_to_plant(self, plant_id: str) -> AuthorizedScope:
        """Apply a client-supplied ``plant_id`` filter.

        Raises:
            ForbiddenError: if no branch can reach that plant.
        """
        narrowed: list[ScopeConstraint] = []
        for branch in self.branches:
            if branch.plant_id is None:
                narrowed.append(
                    ScopeConstraint(
                        tenant_id=branch.tenant_id,
                        plant_id=plant_id,
                        department_id=branch.department_id,
                    )
                )
            elif branch.plant_id == plant_id:
                narrowed.append(branch)
            # A branch pinned to a different plant cannot satisfy the filter.

        if not narrowed:
            record_security_event(
                SecurityEvent.AUTHORIZATION_DENIED,
                reason="plant_out_of_scope",
                requested_plant_id=plant_id,
                check_level="scope",
            )
            raise ForbiddenError()
        return AuthorizedScope(tenant_id=self.tenant_id, branches=tuple(narrowed))

    def narrowed_to_department(self, department_id: str) -> AuthorizedScope:
        """Apply a client-supplied ``department_id`` filter.

        Raises:
            ForbiddenError: if no branch can reach that department.
        """
        narrowed: list[ScopeConstraint] = []
        for branch in self.branches:
            if branch.department_id is None:
                narrowed.append(
                    ScopeConstraint(
                        tenant_id=branch.tenant_id,
                        plant_id=branch.plant_id,
                        department_id=department_id,
                    )
                )
            elif branch.department_id == department_id:
                narrowed.append(branch)

        if not narrowed:
            record_security_event(
                SecurityEvent.AUTHORIZATION_DENIED,
                reason="department_out_of_scope",
                requested_department_id=department_id,
                check_level="scope",
            )
            raise ForbiddenError()
        return AuthorizedScope(tenant_id=self.tenant_id, branches=tuple(narrowed))


def resolve_authorized_scope(
    principal: Principal,
    permission: Permission,
    *,
    requested_tenant_id: str | None = None,
    requested_plant_id: str | None = None,
    requested_department_id: str | None = None,
) -> AuthorizedScope:
    """Resolve the rows this caller may read or write for ``permission``.

    Order of evaluation is deliberate:

    1. **Tenant.** Derived from the authenticated principal. A client-supplied
       tenant that is not the caller's own is refused as 404 by
       ``require_tenant`` before anything else is considered, so a probe cannot
       learn from the status code whether the role would have sufficed.
    2. **Permission.** Only assignments whose role grants ``permission``
       contribute scope. Holding VIEWER on plant-2 must not extend a
       BUDGET_MANAGE request to plant-2.
    3. **Scope branches.** Built from those assignments alone.
    4. **Client filters.** Narrow the result; they can never add a branch.

    Raises:
        CrossTenantAccessError: ``requested_tenant_id`` is another tenant (404).
        ForbiddenError: the caller lacks the permission, holds it at no usable
            scope, or asked for a plant/department outside it (403).
    """
    if requested_tenant_id is not None:
        require_tenant(principal, requested_tenant_id)

    tenant_id = principal.tenant_id
    grants = assignments_granting(principal, permission)
    if not grants:
        record_security_event(
            SecurityEvent.AUTHORIZATION_DENIED,
            reason="permission_not_granted",
            required_permission=str(permission),
            held_roles=sorted(str(role) for role in principal.roles),
            check_level="scope",
        )
        raise ForbiddenError()

    branches: list[ScopeConstraint] = []
    for assignment in grants:
        match assignment.scope_type:
            case ScopeType.TENANT:
                # A TENANT assignment naming another tenant grants nothing here.
                # The token is validly signed but the claim is not authoritative
                # over a tenant the principal does not belong to.
                if assignment.scope_id == tenant_id:
                    branches.append(ScopeConstraint(tenant_id=tenant_id))
            case ScopeType.PLANT:
                branches.append(
                    ScopeConstraint(tenant_id=tenant_id, plant_id=assignment.scope_id)
                )
            case ScopeType.DEPARTMENT:
                branches.append(
                    ScopeConstraint(
                        tenant_id=tenant_id, department_id=assignment.scope_id
                    )
                )

    if not branches:
        record_security_event(
            SecurityEvent.AUTHORIZATION_DENIED,
            reason="permission_held_at_no_usable_scope",
            required_permission=str(permission),
            check_level="scope",
        )
        raise ForbiddenError()

    # A tenant-wide branch subsumes every other branch; keeping the rest would
    # only make the generated SQL longer.
    if any(branch.is_tenant_wide for branch in branches):
        branches = [ScopeConstraint(tenant_id=tenant_id)]

    scope = AuthorizedScope(tenant_id=tenant_id, branches=tuple(dict.fromkeys(branches)))

    if requested_plant_id is not None:
        scope = scope.narrowed_to_plant(requested_plant_id)
    if requested_department_id is not None:
        scope = scope.narrowed_to_department(requested_department_id)

    return scope
