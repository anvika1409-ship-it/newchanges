"""Authenticated principal.

Tenant identity always comes from the authenticated context, never from a
client-supplied field (SECURITY.md section 5, AI_DEVELOPMENT_RULES.md
section 13).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    """Roles defined in SECURITY.md section 4 and DATABASE_SCHEMA.md section 5."""

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
    """A role held at a specific scope.

    A PLANT_MANAGER holds PLANT scope for a specific plant and therefore cannot
    reach another plant's resources (SECURITY.md section 4).
    """

    role: Role
    scope_type: ScopeType
    scope_id: str


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller."""

    subject: str
    tenant_id: str
    assignments: tuple[RoleAssignment, ...] = field(default=())

    @property
    def roles(self) -> frozenset[Role]:
        return frozenset(assignment.role for assignment in self.assignments)

    def has_role(self, *roles: Role) -> bool:
        return bool(self.roles.intersection(roles))

    def scopes_for(self, role: Role, scope_type: ScopeType) -> frozenset[str]:
        """Scope ids where this principal holds ``role`` at ``scope_type``."""
        return frozenset(
            assignment.scope_id
            for assignment in self.assignments
            if assignment.role is role and assignment.scope_type is scope_type
        )
