"""ORM models.

One module per table group, mapped exactly onto DATABASE_SCHEMA.md. Importing
this package registers every mapping with `Base.metadata`, which is what
Alembic autogenerate reads.
"""

# registry (section 11) — pre-existing
# audit (sections 20-21)
from app.db.models.audit import AuditEvent, ModelRegistryHistory

# control plane (sections 3-10)
from app.db.models.control_plane import (
    Agent,
    AgentStatus,
    BusinessPriority,
    Department,
    DepartmentStatus,
    Plant,
    PlantStatus,
    Role,
    ScopeType,
    Tenant,
    TenantStatus,
    User,
    UserRole,
    UserStatus,
    Workload,
    WorkloadStatus,
    WorkloadType,
)

# governance (sections 12-13, 19)
from app.db.models.governance import (
    Approval,
    ApprovalStatus,
    Budget,
    BudgetPeriod,
    BudgetScope,
    RoutingPolicy,
    RoutingPolicyStatus,
)

# intelligence (sections 16-18)
from app.db.models.intelligence import (
    Anomaly,
    Forecast,
    OptimizationRecommendation,
)
from app.db.models.registry import (
    Capability,
    Modality,
    ModelRegistryEntry,
    RiskLevel,
)

# telemetry (sections 14-15)
from app.db.models.telemetry import CostEvent, UsageEvent

# tool registry (section 11.1)
from app.db.models.tools import Tool

__all__ = [
    # registry
    "Capability",
    "Modality",
    "ModelRegistryEntry",
    "RiskLevel",
    # control plane
    "Agent",
    "AgentStatus",
    "BusinessPriority",
    "Department",
    "DepartmentStatus",
    "Plant",
    "PlantStatus",
    "Role",
    "ScopeType",
    "Tenant",
    "TenantStatus",
    "User",
    "UserRole",
    "UserStatus",
    "Workload",
    "WorkloadStatus",
    "WorkloadType",
    # tools
    "Tool",
    # governance
    "Approval",
    "ApprovalStatus",
    "Budget",
    "BudgetPeriod",
    "BudgetScope",
    "RoutingPolicy",
    "RoutingPolicyStatus",
    # telemetry
    "CostEvent",
    "UsageEvent",
    # intelligence
    "Anomaly",
    "Forecast",
    "OptimizationRecommendation",
    # audit
    "AuditEvent",
    "ModelRegistryHistory",
]
