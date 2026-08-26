"""Full schema migration — all remaining tables after the model registry.

Implements DATABASE_SCHEMA.md sections 3-10, 11.1, 12-21 (all tables except
``models`` which was created in 0001_models).

Tables are created in FK-safe order:
  tenants → users/roles/plants → user_roles/departments → workloads →
  agents → tools → budgets → routing_policies → usage_events →
  cost_events → forecasts → anomalies → optimization_recommendations →
  approvals → audit_events → model_registry_history

Indexes match exactly those documented in DATABASE_SCHEMA.md section 14 for
usage_events and the schema notes for other tables.

Revision ID: 0002_full_schema
Revises: 0001_models
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_full_schema"
down_revision: str | None = "0001_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # tenants — DATABASE_SCHEMA.md section 3
    # ------------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','SUSPENDED','INACTIVE')",
            name=op.f("ck_tenants_tenant_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )

    # ------------------------------------------------------------------
    # roles — DATABASE_SCHEMA.md section 5
    # ------------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.UniqueConstraint("name", name=op.f("uq_roles_name")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
    )

    # ------------------------------------------------------------------
    # users — DATABASE_SCHEMA.md section 4
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','DISABLED','PENDING')",
            name=op.f("ck_users_user_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_users_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"], unique=False)

    # ------------------------------------------------------------------
    # plants — DATABASE_SCHEMA.md section 7
    # ------------------------------------------------------------------
    op.create_table(
        "plants",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','INACTIVE','MAINTENANCE')",
            name=op.f("ck_plants_plant_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_plants_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plants")),
    )
    op.create_index(op.f("ix_plants_tenant_id"), "plants", ["tenant_id"], unique=False)

    # ------------------------------------------------------------------
    # user_roles — DATABASE_SCHEMA.md section 6
    # ------------------------------------------------------------------
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role_id", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.CheckConstraint(
            "scope_type IN ('TENANT','PLANT','DEPARTMENT')",
            name=op.f("ck_user_roles_user_role_scope_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_user_roles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"],
            name=op.f("fk_user_roles_role_id_roles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "role_id", "scope_type", "scope_id",
            name=op.f("pk_user_roles"),
        ),
    )

    # ------------------------------------------------------------------
    # departments — DATABASE_SCHEMA.md section 8
    # ------------------------------------------------------------------
    op.create_table(
        "departments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("plant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','INACTIVE')",
            name=op.f("ck_departments_department_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["plant_id"], ["plants.id"],
            name=op.f("fk_departments_plant_id_plants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
    )
    op.create_index(op.f("ix_departments_plant_id"), "departments", ["plant_id"], unique=False)

    # ------------------------------------------------------------------
    # workloads — DATABASE_SCHEMA.md section 9
    # ------------------------------------------------------------------
    op.create_table(
        "workloads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("plant_id", sa.String(), nullable=False),
        sa.Column("department_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("workload_type", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("business_priority", sa.String(), nullable=False),
        sa.Column("risk_level", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "workload_type IN ('quality_check','predictive_maintenance','supply_chain')",
            name=op.f("ck_workloads_workload_type_valid"),
        ),
        sa.CheckConstraint(
            "business_priority IN ('LOW','NORMAL','HIGH','CRITICAL')",
            name=op.f("ck_workloads_workload_business_priority_valid"),
        ),
        sa.CheckConstraint(
            "risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name=op.f("ck_workloads_workload_risk_level_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','INACTIVE','PAUSED')",
            name=op.f("ck_workloads_workload_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["plant_id"], ["plants.id"],
            name=op.f("fk_workloads_plant_id_plants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"],
            name=op.f("fk_workloads_department_id_departments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workloads")),
    )
    op.create_index(op.f("ix_workloads_plant_id"), "workloads", ["plant_id"], unique=False)
    op.create_index(op.f("ix_workloads_department_id"), "workloads", ["department_id"], unique=False)
    op.create_index(op.f("ix_workloads_workload_type"), "workloads", ["workload_type"], unique=False)

    # ------------------------------------------------------------------
    # agents — DATABASE_SCHEMA.md section 10
    # ------------------------------------------------------------------
    op.create_table(
        "agents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workload_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("agent_type", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("default_model_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','INACTIVE','DEPRECATED')",
            name=op.f("ck_agents_agent_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workload_id"], ["workloads.id"],
            name=op.f("fk_agents_workload_id_workloads"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
    )
    op.create_index(op.f("ix_agents_workload_id"), "agents", ["workload_id"], unique=False)
    op.create_index(op.f("ix_agents_agent_type"), "agents", ["agent_type"], unique=False)

    # ------------------------------------------------------------------
    # tools — DATABASE_SCHEMA.md section 11.1
    # ------------------------------------------------------------------
    op.create_table(
        "tools",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("allowed_roles", sa.String(), nullable=False),
        sa.Column("allowed_workloads", sa.String(), nullable=True),
        sa.Column("risk_level", sa.String(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name=op.f("ck_tools_tool_risk_level_valid"),
        ),
        sa.CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name=op.f("ck_tools_tool_estimated_cost_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_tools_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("name", name=op.f("uq_tools_name")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tools")),
    )
    op.create_index(op.f("ix_tools_tenant_id"), "tools", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_tools_name"), "tools", ["name"], unique=True)

    # ------------------------------------------------------------------
    # budgets — DATABASE_SCHEMA.md section 12
    # ------------------------------------------------------------------
    op.create_table(
        "budgets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column("warning_threshold_percent", sa.Float(), nullable=False),
        sa.Column("critical_threshold_percent", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "scope_type IN ('ENTERPRISE','TENANT','PLANT','DEPARTMENT','WORKLOAD','AGENT','MODEL')",
            name=op.f("ck_budgets_budget_scope_type_valid"),
        ),
        sa.CheckConstraint(
            "period IN ('DAILY','MONTHLY','QUARTERLY','ANNUAL')",
            name=op.f("ck_budgets_budget_period_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','INACTIVE','EXCEEDED')",
            name=op.f("ck_budgets_budget_status_valid"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_budgets_budget_amount_positive")),
        sa.CheckConstraint(
            "warning_threshold_percent > 0 AND warning_threshold_percent <= 100",
            name=op.f("ck_budgets_budget_warning_threshold_valid"),
        ),
        sa.CheckConstraint(
            "critical_threshold_percent > 0 AND critical_threshold_percent <= 100",
            name=op.f("ck_budgets_budget_critical_threshold_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_budgets_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
    )
    op.create_index(op.f("ix_budgets_tenant_id"), "budgets", ["tenant_id"], unique=False)

    # ------------------------------------------------------------------
    # routing_policies — DATABASE_SCHEMA.md section 13
    # ------------------------------------------------------------------
    op.create_table(
        "routing_policies",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("workload_type", sa.String(), nullable=False),
        sa.Column("complexity", sa.String(), nullable=False),
        sa.Column("business_priority", sa.String(), nullable=True),
        sa.Column("selected_model_id", sa.String(), nullable=True),
        sa.Column("selected_agent_id", sa.String(), nullable=True),
        sa.Column("max_context_tokens", sa.Integer(), nullable=True),
        sa.Column("max_tool_calls", sa.Integer(), nullable=True),
        sa.Column("max_cost_per_request", sa.Float(), nullable=True),
        sa.Column("max_total_tokens_per_request", sa.Integer(), nullable=True),
        sa.Column("minimum_quality_score", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("canary_traffic_percent", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT','PENDING_APPROVAL','CANARY','ACTIVE','SUPERSEDED','ROLLED_BACK')",
            name=op.f("ck_routing_policies_routing_policy_status_valid"),
        ),
        sa.CheckConstraint(
            "complexity IN ('simple','medium','complex')",
            name=op.f("ck_routing_policies_routing_policy_complexity_valid"),
        ),
        sa.CheckConstraint(
            "canary_traffic_percent IS NULL OR "
            "(canary_traffic_percent >= 0 AND canary_traffic_percent <= 100)",
            name=op.f("ck_routing_policies_routing_policy_canary_traffic_valid"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_routing_policies_routing_policy_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_routing_policies_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["selected_model_id"], ["models.id"],
            name=op.f("fk_routing_policies_selected_model_id_models"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["selected_agent_id"], ["agents.id"],
            name=op.f("fk_routing_policies_selected_agent_id_agents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routing_policies")),
    )
    op.create_index(
        op.f("ix_routing_policies_tenant_id"), "routing_policies", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_routing_policies_workload_type"),
        "routing_policies", ["workload_type"], unique=False,
    )

    # ------------------------------------------------------------------
    # usage_events — DATABASE_SCHEMA.md section 14
    # Indexes: timestamp, tenant_id, user_id, plant_id, workload_id,
    #          agent_id, model_id, request_id
    # ------------------------------------------------------------------
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("plant_id", sa.String(), nullable=True),
        sa.Column("department_id", sa.String(), nullable=True),
        sa.Column("workload_id", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("context_tokens", sa.Integer(), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=True),
        sa.Column("tool_calls", sa.Integer(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("model_latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("business_priority", sa.String(), nullable=True),
        sa.Column("risk_level", sa.String(), nullable=True),
        sa.Column("routing_policy_version", sa.Integer(), nullable=True),
        sa.Column("budget_decision", sa.String(), nullable=True),
        sa.Column("guardrail_decision", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "budget_decision IS NULL OR "
            "budget_decision IN ('ALLOW','DOWNGRADE','REQUIRE_APPROVAL','BLOCK')",
            name=op.f("ck_usage_events_usage_event_budget_decision_valid"),
        ),
        sa.CheckConstraint(
            "status IS NULL OR "
            "status IN ('SUCCESS','FAILURE','TIMEOUT','BLOCKED','DOWNGRADED')",
            name=op.f("ck_usage_events_usage_event_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
    )
    # Required indexes per DATABASE_SCHEMA.md section 14
    op.create_index(op.f("ix_usage_events_timestamp"), "usage_events", ["timestamp"], unique=False)
    op.create_index(op.f("ix_usage_events_tenant_id"), "usage_events", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_usage_events_user_id"), "usage_events", ["user_id"], unique=False)
    op.create_index(op.f("ix_usage_events_plant_id"), "usage_events", ["plant_id"], unique=False)
    op.create_index(op.f("ix_usage_events_workload_id"), "usage_events", ["workload_id"], unique=False)
    op.create_index(op.f("ix_usage_events_agent_id"), "usage_events", ["agent_id"], unique=False)
    op.create_index(op.f("ix_usage_events_model_id"), "usage_events", ["model_id"], unique=False)
    op.create_index(op.f("ix_usage_events_request_id"), "usage_events", ["request_id"], unique=False)

    # ------------------------------------------------------------------
    # cost_events — DATABASE_SCHEMA.md section 15
    # ------------------------------------------------------------------
    op.create_table(
        "cost_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("usage_event_id", sa.String(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("actual_cost", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("provenance", sa.String(), nullable=False),
        sa.Column("input_cost", sa.Float(), nullable=True),
        sa.Column("output_cost", sa.Float(), nullable=True),
        sa.Column("tool_cost", sa.Float(), nullable=True),
        sa.Column("infrastructure_cost", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "provenance IN ('ACTUAL','ESTIMATED','UNAVAILABLE')",
            name=op.f("ck_cost_events_cost_event_provenance_valid"),
        ),
        sa.CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name=op.f("ck_cost_events_cost_event_estimated_cost_non_negative"),
        ),
        sa.CheckConstraint(
            "actual_cost IS NULL OR actual_cost >= 0",
            name=op.f("ck_cost_events_cost_event_actual_cost_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["usage_event_id"], ["usage_events.id"],
            name=op.f("fk_cost_events_usage_event_id_usage_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cost_events")),
        sa.UniqueConstraint("usage_event_id", name=op.f("uq_cost_events_usage_event_id")),
    )
    op.create_index(
        op.f("ix_cost_events_usage_event_id"), "cost_events", ["usage_event_id"], unique=True
    )

    # ------------------------------------------------------------------
    # forecasts — DATABASE_SCHEMA.md section 16
    # ------------------------------------------------------------------
    op.create_table(
        "forecasts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=True),
        sa.Column("scope_id", sa.String(), nullable=True),
        sa.Column("forecast_date", sa.Date(), nullable=True),
        sa.Column("predicted_cost", sa.Float(), nullable=True),
        sa.Column("lower_bound", sa.Float(), nullable=True),
        sa.Column("upper_bound", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("forecast_model_name", sa.String(), nullable=True),
        sa.Column("forecast_model_version", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_forecasts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_forecasts")),
    )
    op.create_index(op.f("ix_forecasts_tenant_id"), "forecasts", ["tenant_id"], unique=False)

    # ------------------------------------------------------------------
    # anomalies — DATABASE_SCHEMA.md section 17
    # ------------------------------------------------------------------
    op.create_table(
        "anomalies",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.Column("scope_type", sa.String(), nullable=True),
        sa.Column("scope_id", sa.String(), nullable=True),
        sa.Column("anomaly_type", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("deviation_percent", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "severity IS NULL OR severity IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name=op.f("ck_anomalies_anomaly_severity_valid"),
        ),
        sa.CheckConstraint(
            "status IS NULL OR "
            "status IN ('OPEN','ACKNOWLEDGED','RESOLVED','FALSE_POSITIVE')",
            name=op.f("ck_anomalies_anomaly_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_anomalies_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomalies")),
    )
    op.create_index(op.f("ix_anomalies_tenant_id"), "anomalies", ["tenant_id"], unique=False)

    # ------------------------------------------------------------------
    # approvals — DATABASE_SCHEMA.md section 19
    # (created before optimization_recommendations which FKs to routing_policies)
    # ------------------------------------------------------------------
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("risk_level", sa.String(), nullable=True),
        sa.Column("requested_by", sa.String(), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("comments", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','EXPIRED')",
            name=op.f("ck_approvals_approval_status_valid"),
        ),
        sa.CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name=op.f("ck_approvals_approval_risk_level_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_approvals_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    op.create_index(op.f("ix_approvals_tenant_id"), "approvals", ["tenant_id"], unique=False)

    # ------------------------------------------------------------------
    # optimization_recommendations — DATABASE_SCHEMA.md section 18
    # ------------------------------------------------------------------
    op.create_table(
        "optimization_recommendations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("workload_id", sa.String(), nullable=True),
        sa.Column("current_strategy", sa.String(), nullable=True),
        sa.Column("recommended_strategy", sa.String(), nullable=True),
        sa.Column("estimated_saving", sa.Float(), nullable=True),
        sa.Column("estimated_saving_percent", sa.Float(), nullable=True),
        sa.Column("quality_impact_percent", sa.Float(), nullable=True),
        sa.Column("latency_impact_percent", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(), nullable=True),
        sa.Column("recommendation_reason", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("applied_policy_id", sa.String(), nullable=True),
        sa.Column("superseded_policy_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT','PENDING_APPROVAL','APPROVED','REJECTED','APPLIED','ROLLED_BACK')",
            name=op.f("ck_optimization_recommendations_opt_rec_status_valid"),
        ),
        sa.CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name=op.f("ck_optimization_recommendations_opt_rec_risk_level_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name=op.f("fk_optimization_recommendations_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workload_id"], ["workloads.id"],
            name=op.f("fk_optimization_recommendations_workload_id_workloads"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["applied_policy_id"], ["routing_policies.id"],
            name=op.f("fk_optimization_recommendations_applied_policy_id_routing_policies"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_policy_id"], ["routing_policies.id"],
            name=op.f("fk_optimization_recommendations_superseded_policy_id_routing_policies"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_optimization_recommendations")),
    )
    op.create_index(
        op.f("ix_optimization_recommendations_tenant_id"),
        "optimization_recommendations", ["tenant_id"], unique=False,
    )
    op.create_index(
        op.f("ix_optimization_recommendations_workload_id"),
        "optimization_recommendations", ["workload_id"], unique=False,
    )

    # ------------------------------------------------------------------
    # audit_events — DATABASE_SCHEMA.md section 20
    # ------------------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=True),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("before_state", sa.String(), nullable=True),
        sa.Column("after_state", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("approval_id", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.id"],
            name=op.f("fk_audit_events_approval_id_approvals"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_tenant_id"), "audit_events", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"], unique=False)

    # ------------------------------------------------------------------
    # model_registry_history — DATABASE_SCHEMA.md section 21
    # ------------------------------------------------------------------
    op.create_table(
        "model_registry_history",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("change_type", sa.String(), nullable=True),
        sa.Column("old_value", sa.String(), nullable=True),
        sa.Column("new_value", sa.String(), nullable=True),
        sa.Column("changed_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_registry_history")),
    )
    op.create_index(
        op.f("ix_model_registry_history_model_id"),
        "model_registry_history", ["model_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_table("model_registry_history")
    op.drop_table("audit_events")
    op.drop_table("optimization_recommendations")
    op.drop_table("approvals")
    op.drop_table("anomalies")
    op.drop_table("forecasts")
    op.drop_table("cost_events")
    op.drop_table("usage_events")
    op.drop_table("budgets")
    op.drop_table("routing_policies")
    op.drop_table("tools")
    op.drop_table("agents")
    op.drop_table("workloads")
    op.drop_table("departments")
    op.drop_table("user_roles")
    op.drop_table("plants")
    op.drop_table("users")
    op.drop_table("roles")
    op.drop_table("tenants")
