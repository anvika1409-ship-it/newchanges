"""ORM mapping for routing_policies table.

Implements DATABASE_SCHEMA.md section 13:
Enforces immutable versioning, policy statuses (DRAFT, PENDING_APPROVAL, CANARY,
ACTIVE, SUPERSEDED, ROLLED_BACK), and request-level limits.
"""

from __future__ import annotations

from enum import StrEnum

from app.db.models.governance import RoutingPolicy


class PolicyStatus(StrEnum):
    """Routing policy lifecycle status (DATABASE_SCHEMA.md section 13)."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    CANARY = "CANARY"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ROLLED_BACK = "ROLLED_BACK"


# Alias mapped to canonical schema model in app.db.models.governance
RoutingPolicyRecord = RoutingPolicy
