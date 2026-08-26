"""Authenticated principal and scoped role assignments.

Tenant identity always comes from the authenticated context, never from a
client-supplied field (SECURITY.md section 5, AI_DEVELOPMENT_RULES.md
section 13).

A role assignment is always scoped, matching ``user_roles`` in
DATABASE_SCHEMA.md section 6. That is what makes the SECURITY.md section 4
example enforceable: a PLANT_MANAGER holds the role at PLANT scope for one
plant and therefore cannot reach another plant's resources.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """Roles from SECURITY.md section 4 and DATABASE_SCHEMA.md section 5."""

    ADMIN = "ADMIN"
    FINOPS_MANAGER = "FINOPS_MANAGER"
    AI_ENGINEER = "AI_ENGINEER"
    PLANT_MANAGER = "PLANT_MANAGER"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"


class ScopeType(StrEnum):
    """Scope of a role assignment (DATABASE_SCHEMA.md section 6)."""

    TENANT = "TENANT"
    PLANT = "PLANT"
    DEPARTMENT = "DEPARTMENT"


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    """One role held at one scope."""

    role: Role
    scope_type: ScopeType
    scope_id: str


@dataclass(frozen=True, slots=True)
class ResourceScope:
    """Ownership of the resource being accessed.

    ``plant_id`` and ``department_id`` are None for tenant-wide resources such
    as an enterprise budget or a cross-plant cost summary.
    """

    tenant_id: str
    plant_id: str | None = None
    department_id: str | None = None


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller."""

    subject: str
    tenant_id: str
    assignments: tuple[RoleAssignment, ...] = ()

    @property
    def roles(self) -> frozenset[Role]:
        return frozenset(assignment.role for assignment in self.assignments)

    def has_role(self, *roles: Role) -> bool:
        """Endpoint-level check: does the caller hold any of these roles at all?

        Holding a role is not the same as being allowed to touch a particular
        record. Resource-level scope is evaluated separately in
        ``app.security.authorization``.
        """
        return bool(self.roles.intersection(roles))

    def assignments_for(self, *roles: Role) -> tuple[RoleAssignment, ...]:
        if not roles:
            return self.assignments
        return tuple(a for a in self.assignments if a.role in roles)

    def scope_ids(self, role: Role, scope_type: ScopeType) -> frozenset[str]:
        """Scope ids where this principal holds ``role`` at ``scope_type``.

        Used to constrain queries to the rows a caller may see, rather than
        filtering after the fact.
        """
        return frozenset(
            a.scope_id
            for a in self.assignments
            if a.role is role and a.scope_type is scope_type
        )

    def accessible_plant_ids(self, *roles: Role) -> frozenset[str]:
        """Plant ids reachable through a PLANT-scoped assignment.

        An empty set does not mean "no access": a TENANT-scoped assignment
        grants every plant without enumerating them. Check
        ``has_tenant_wide_access`` first.
        """
        return frozenset(
            a.scope_id
            for a in self.assignments_for(*roles)
            if a.scope_type is ScopeType.PLANT
        )

    def has_tenant_wide_access(self, *roles: Role) -> bool:
        return any(
            a.scope_type is ScopeType.TENANT and a.scope_id == self.tenant_id
            for a in self.assignments_for(*roles)
        )
