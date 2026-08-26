"""Control-plane ORM models.

Implements DATABASE_SCHEMA.md sections 3-10:
  tenants, users, roles, user_roles, plants, departments, workloads, agents.

Column names, types and nullability match the schema exactly. No column is
added, renamed, or defaulted without a documented reason
(AI_DEVELOPMENT_RULES.md sections 2, 34, 35).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Enums (stored as TEXT in SQLite, validated via CheckConstraint)
# ---------------------------------------------------------------------------

class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    PENDING = "PENDING"


class ScopeType(StrEnum):
    """Scope type for user_roles and budgets (DATABASE_SCHEMA.md sections 6, 12)."""
    TENANT = "TENANT"
    PLANT = "PLANT"
    DEPARTMENT = "DEPARTMENT"


class PlantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"


class DepartmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class WorkloadType(StrEnum):
    """DATABASE_SCHEMA.md section 9."""
    QUALITY_CHECK = "quality_check"
    PREDICTIVE_MAINTENANCE = "predictive_maintenance"
    SUPPLY_CHAIN = "supply_chain"


class BusinessPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WorkloadStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    PAUSED = "PAUSED"


class AgentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DEPRECATED = "DEPRECATED"


# ---------------------------------------------------------------------------
# tenants — DATABASE_SCHEMA.md section 3
# ---------------------------------------------------------------------------

class Tenant(Base):
    """One enterprise/customer boundary."""

    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','SUSPENDED','INACTIVE')",
            name="tenant_status_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default=TenantStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # relationships
    users: Mapped[list[User]] = relationship("User", back_populates="tenant")
    plants: Mapped[list[Plant]] = relationship("Plant", back_populates="tenant")

    def __repr__(self) -> str:  # pragma: no cover
        return f"Tenant(id={self.id!r}, name={self.name!r})"


# ---------------------------------------------------------------------------
# users — DATABASE_SCHEMA.md section 4
# ---------------------------------------------------------------------------

class User(Base):
    """Platform user, always scoped to a tenant."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint(
            "status IN ('ACTIVE','DISABLED','PENDING')",
            name="user_status_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default=UserStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # relationships
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")
    user_roles: Mapped[list[UserRole]] = relationship("UserRole", back_populates="user")

    def __repr__(self) -> str:  # pragma: no cover
        return f"User(id={self.id!r}, username={self.username!r})"


# ---------------------------------------------------------------------------
# roles — DATABASE_SCHEMA.md section 5
# ---------------------------------------------------------------------------

class Role(Base):
    """Named role. Six suggested roles are seeded; custom roles can be added."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("name", name="uq_roles_name"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    # relationships
    user_roles: Mapped[list[UserRole]] = relationship("UserRole", back_populates="role")

    def __repr__(self) -> str:  # pragma: no cover
        return f"Role(id={self.id!r}, name={self.name!r})"


# ---------------------------------------------------------------------------
# user_roles — DATABASE_SCHEMA.md section 6
# ---------------------------------------------------------------------------

class UserRole(Base):
    """Scoped role assignment.

    The composite PK (user_id, role_id, scope_type, scope_id) mirrors the
    schema exactly so a user can hold the same role at different scopes without
    a unique-constraint clash.
    """

    __tablename__ = "user_roles"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "role_id", "scope_type", "scope_id",
                             name="pk_user_roles"),
        CheckConstraint(
            "scope_type IN ('TENANT','PLANT','DEPARTMENT')",
            name="user_role_scope_type_valid",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[str] = mapped_column(
        String, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String, nullable=False)
    scope_id: Mapped[str] = mapped_column(String, nullable=False)

    # relationships
    user: Mapped[User] = relationship("User", back_populates="user_roles")
    role: Mapped[Role] = relationship("Role", back_populates="user_roles")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"UserRole(user_id={self.user_id!r}, role_id={self.role_id!r}, "
            f"scope_type={self.scope_type!r}, scope_id={self.scope_id!r})"
        )


# ---------------------------------------------------------------------------
# plants — DATABASE_SCHEMA.md section 7
# ---------------------------------------------------------------------------

class Plant(Base):
    """A manufacturing plant owned by a tenant."""

    __tablename__ = "plants"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','INACTIVE','MAINTENANCE')",
            name="plant_status_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default=PlantStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # relationships
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="plants")
    departments: Mapped[list[Department]] = relationship("Department", back_populates="plant")
    workloads: Mapped[list[Workload]] = relationship("Workload", back_populates="plant")

    def __repr__(self) -> str:  # pragma: no cover
        return f"Plant(id={self.id!r}, name={self.name!r})"


# ---------------------------------------------------------------------------
# departments — DATABASE_SCHEMA.md section 8
# ---------------------------------------------------------------------------

class Department(Base):
    """A department within a plant."""

    __tablename__ = "departments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','INACTIVE')",
            name="department_status_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    plant_id: Mapped[str] = mapped_column(
        String, ForeignKey("plants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default=DepartmentStatus.ACTIVE)

    # relationships
    plant: Mapped[Plant] = relationship("Plant", back_populates="departments")
    workloads: Mapped[list[Workload]] = relationship("Workload", back_populates="department")

    def __repr__(self) -> str:  # pragma: no cover
        return f"Department(id={self.id!r}, name={self.name!r})"


# ---------------------------------------------------------------------------
# workloads — DATABASE_SCHEMA.md section 9
# ---------------------------------------------------------------------------

class Workload(Base):
    """An AI business workload associated with a plant and department."""

    __tablename__ = "workloads"
    __table_args__ = (
        CheckConstraint(
            "workload_type IN ('quality_check','predictive_maintenance','supply_chain')",
            name="workload_type_valid",
        ),
        CheckConstraint(
            "business_priority IN ('LOW','NORMAL','HIGH','CRITICAL')",
            name="workload_business_priority_valid",
        ),
        CheckConstraint(
            "risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="workload_risk_level_valid",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','INACTIVE','PAUSED')",
            name="workload_status_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    plant_id: Mapped[str] = mapped_column(
        String, ForeignKey("plants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    department_id: Mapped[str] = mapped_column(
        String, ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    workload_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    business_priority: Mapped[str] = mapped_column(
        String, nullable=False, default=BusinessPriority.NORMAL
    )
    risk_level: Mapped[str] = mapped_column(String, nullable=False, default=RiskLevel.LOW)
    status: Mapped[str] = mapped_column(String, nullable=False, default=WorkloadStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # relationships
    plant: Mapped[Plant] = relationship("Plant", back_populates="workloads")
    department: Mapped[Department] = relationship("Department", back_populates="workloads")
    agents: Mapped[list[Agent]] = relationship("Agent", back_populates="workload")

    def __repr__(self) -> str:  # pragma: no cover
        return f"Workload(id={self.id!r}, name={self.name!r}, type={self.workload_type!r})"


# ---------------------------------------------------------------------------
# agents — DATABASE_SCHEMA.md section 10
# ---------------------------------------------------------------------------

class Agent(Base):
    """A logical agent capability associated with a workload."""

    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','INACTIVE','DEPRECATED')",
            name="agent_status_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workload_id: Mapped[str] = mapped_column(
        String, ForeignKey("workloads.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    agent_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # FK to models.id: nullable — an agent can exist before a model is assigned.
    # Not declared as a SQLAlchemy ForeignKey to keep telemetry tables
    # (which denormalize this) from creating a hard dependency.
    default_model_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default=AgentStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # relationships
    workload: Mapped[Workload] = relationship("Workload", back_populates="agents")

    def __repr__(self) -> str:  # pragma: no cover
        return f"Agent(id={self.id!r}, name={self.name!r}, type={self.agent_type!r})"
