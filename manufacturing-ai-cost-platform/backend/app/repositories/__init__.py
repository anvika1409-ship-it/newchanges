"""Repository layer.

All database access goes through these repository classes. Business services
and API routes must not write SQL directly
(AI_DEVELOPMENT_RULES.md section 16, DATABASE_SCHEMA.md section 1).
"""

from app.repositories.audit_repository import AuditEventRepository, ModelRegistryHistoryRepository
from app.repositories.base import AsyncRepository
from app.repositories.budget_repository import BudgetRepository
from app.repositories.forecast_repository import AnomalyRepository, ForecastRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.optimization_repository import (
    ApprovalRepository,
    OptimizationRecommendationRepository,
)
from app.repositories.plant_repository import DepartmentRepository, PlantRepository
from app.repositories.routing_policy_repository import RoutingPolicyRepository
from app.repositories.telemetry_repository import CostEventRepository, UsageEventRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import RoleRepository, UserRepository, UserRoleRepository
from app.repositories.workload_repository import AgentRepository, WorkloadRepository

__all__ = [
    "AsyncRepository",
    "AuditEventRepository",
    "AnomalyRepository",
    "AgentRepository",
    "ApprovalRepository",
    "BudgetRepository",
    "CostEventRepository",
    "DepartmentRepository",
    "ForecastRepository",
    "ModelRegistryHistoryRepository",
    "ModelRepository",
    "OptimizationRecommendationRepository",
    "PlantRepository",
    "RoleRepository",
    "RoutingPolicyRepository",
    "TenantRepository",
    "UsageEventRepository",
    "UserRepository",
    "UserRoleRepository",
    "WorkloadRepository",
]
