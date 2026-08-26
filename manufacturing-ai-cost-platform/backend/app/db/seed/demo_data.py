"""Demo seed data.

Inserts one complete demo enterprise with three plants, departments, workloads,
agents, routing policies and budgets into the database.

IMPORTANT — all data here is SIMULATED / DEMO.
No real enterprise, no real cost figures, no real AI execution outcomes.
Labels required by AI_DEVELOPMENT_RULES.md sections 41-42 and
DATABASE_SCHEMA.md sections 15-16 are applied in every record that carries
a numeric value.

Usage:
    python -m app.db.seed           # uses DATABASE_URL from environment

The seed function is idempotent: if the demo tenant already exists the
function returns immediately without inserting duplicates.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AuditEvent
from app.db.models.control_plane import (
    Agent,
    Department,
    Plant,
    Role,
    Tenant,
    User,
    UserRole,
    Workload,
)
from app.db.models.governance import (
    Budget,
    RoutingPolicy,
)
from app.db.models.intelligence import Forecast, OptimizationRecommendation
from app.db.models.telemetry import CostEvent, UsageEvent
from app.repositories.tenant_repository import TenantRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEMO_TENANT_ID = "tenant-acme-manufacturing"

# GenAILab model IDs from the existing seed (ARCHITECTURE.md section 8).
# IDs are assigned deterministically so they match what 0001_models inserted.
MODEL_VISION_LLAMA = "model-vision-llama-90b"
MODEL_VISION_PHI = "model-vision-phi-35"
MODEL_EMBEDDING = "model-embedding-3-large"
MODEL_SPEECH = "model-speech-whisper"

_NOW = datetime(2026, 8, 26, 8, 0, 0, tzinfo=UTC)


def _id() -> str:
    return str(uuid.uuid4())


def _ts(delta_days: int = 0) -> datetime:
    return _NOW + timedelta(days=delta_days)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_seed(session: AsyncSession) -> None:
    """Insert demo data if the demo tenant does not already exist.

    All numeric cost and quality values are SIMULATED.
    """
    repo = TenantRepository(session)
    if await repo.exists(DEMO_TENANT_ID):
        logger.info("seed_skipped", extra={"reason": "demo tenant already present"})
        return

    logger.info("seed_start", extra={"tenant_id": DEMO_TENANT_ID})

    # ------------------------------------------------------------------ tenant
    tenant = Tenant(
        id=DEMO_TENANT_ID,
        name="ACME Manufacturing (DEMO)",
        status="ACTIVE",
        created_at=_ts(-30),
        updated_at=_ts(-30),
    )
    session.add(tenant)

    # ------------------------------------------------------------------ roles
    role_ids: dict[str, str] = {}
    for role_name, desc in [
        ("ADMIN", "Platform administrator"),
        ("FINOPS_MANAGER", "FinOps cost manager"),
        ("AI_ENGINEER", "AI/ML engineer"),
        ("PLANT_MANAGER", "Manufacturing plant manager"),
        ("ANALYST", "Cost and quality analyst"),
        ("VIEWER", "Read-only viewer"),
    ]:
        rid = f"role-{role_name.lower().replace('_', '-')}"
        role_ids[role_name] = rid
        session.add(Role(id=rid, name=role_name, description=desc))

    # ------------------------------------------------------------------ users
    admin_user_id = "user-admin-demo"
    session.add(
        User(
            id=admin_user_id,
            tenant_id=DEMO_TENANT_ID,
            username="admin@acme.demo",
            email="admin@acme.demo",
            status="ACTIVE",
            created_at=_ts(-30),
            updated_at=_ts(-30),
        )
    )
    # Tenant-wide ADMIN role for demo admin user
    session.add(
        UserRole(
            user_id=admin_user_id,
            role_id=role_ids["ADMIN"],
            scope_type="TENANT",
            scope_id=DEMO_TENANT_ID,
        )
    )

    # ------------------------------------------------------------------ plants
    plants: list[tuple[str, str, str, str]] = [
        ("plant-pune", "Pune Plant", "Pune, India", "Asia/Kolkata"),
        ("plant-chennai", "Chennai Plant", "Chennai, India", "Asia/Kolkata"),
        ("plant-bangalore", "Bangalore Plant", "Bangalore, India", "Asia/Kolkata"),
    ]
    plant_objs: dict[str, Plant] = {}
    for plant_id, name, location, tz in plants:
        p = Plant(
            id=plant_id,
            tenant_id=DEMO_TENANT_ID,
            name=name,
            location=location,
            timezone=tz,
            status="ACTIVE",
            created_at=_ts(-30),
            updated_at=_ts(-30),
        )
        session.add(p)
        plant_objs[plant_id] = p

    # ------------------------------------------------------------------ departments (3 per plant)
    dept_specs: list[tuple[str, str]] = [
        ("Quality", "ACTIVE"),
        ("Maintenance", "ACTIVE"),
        ("Supply Chain", "ACTIVE"),
    ]
    dept_objs: dict[str, Department] = {}
    for plant_id, _, _, _ in plants:
        for dept_name, dept_status in dept_specs:
            dept_id = f"dept-{plant_id}-{dept_name.lower().replace(' ', '-')}"
            d = Department(
                id=dept_id,
                plant_id=plant_id,
                name=dept_name,
                status=dept_status,
            )
            session.add(d)
            dept_objs[dept_id] = d

    # ------------------------------------------------------------------ workloads
    workload_specs = [
        ("quality_check", "Quality", "Quality Control AI", "HIGH", "MEDIUM"),
        ("predictive_maintenance", "Maintenance", "Predictive Maintenance AI", "NORMAL", "LOW"),
        ("supply_chain", "Supply Chain", "Supply Chain Optimizer", "NORMAL", "LOW"),
    ]
    workload_objs: dict[str, Workload] = {}
    for plant_id, _, _, _ in plants:
        for wl_type, dept_name, wl_name, priority, risk in workload_specs:
            dept_id = f"dept-{plant_id}-{dept_name.lower().replace(' ', '-')}"
            wl_id = f"wl-{plant_id}-{wl_type}"
            wl = Workload(
                id=wl_id,
                plant_id=plant_id,
                department_id=dept_id,
                name=wl_name,
                workload_type=wl_type,
                description=f"{wl_name} workload — SIMULATED DEMO",
                business_priority=priority,
                risk_level=risk,
                status="ACTIVE",
                created_at=_ts(-25),
                updated_at=_ts(-25),
            )
            session.add(wl)
            workload_objs[wl_id] = wl

    # ------------------------------------------------------------------ agents
    agent_specs = [
        ("quality_check", "QualityInspectorAgent", "vision_inspector"),
        ("predictive_maintenance", "MaintenanceAdvisorAgent", "maintenance_advisor"),
        ("supply_chain", "SupplyChainAgent", "supply_chain_optimizer"),
    ]
    agent_ids: dict[str, str] = {}
    for plant_id, _, _, _ in plants:
        for wl_type, agent_name, agent_type in agent_specs:
            wl_id = f"wl-{plant_id}-{wl_type}"
            agent_id = f"agent-{plant_id}-{wl_type}"
            # Default model assignment: vision workloads -> Llama vision model
            default_model = MODEL_VISION_LLAMA if wl_type == "quality_check" else None
            session.add(
                Agent(
                    id=agent_id,
                    workload_id=wl_id,
                    name=agent_name,
                    agent_type=agent_type,
                    description=f"{agent_name} — SIMULATED DEMO",
                    default_model_id=default_model,
                    status="ACTIVE",
                    created_at=_ts(-25),
                    updated_at=_ts(-25),
                )
            )
            agent_ids[f"{plant_id}-{wl_type}"] = agent_id

    # ------------------------------------------------------------------ budgets (SIMULATED)
    #
    # All monetary values below are SIMULATED demo figures.
    # They do not represent actual enterprise spending.
    #
    tenant_budget_id = "budget-tenant-monthly"
    session.add(
        Budget(
            id=tenant_budget_id,
            tenant_id=DEMO_TENANT_ID,
            scope_type="TENANT",
            scope_id=DEMO_TENANT_ID,
            amount=5000.0,          # SIMULATED
            currency="USD",
            period="MONTHLY",
            warning_threshold_percent=80.0,
            critical_threshold_percent=95.0,
            status="ACTIVE",
            created_at=_ts(-30),
            updated_at=_ts(-30),
        )
    )

    for plant_id, _plant_name, _, _ in plants:
        session.add(
            Budget(
                id=f"budget-{plant_id}-monthly",
                tenant_id=DEMO_TENANT_ID,
                scope_type="PLANT",
                scope_id=plant_id,
                amount=1500.0,       # SIMULATED
                currency="USD",
                period="MONTHLY",
                warning_threshold_percent=80.0,
                critical_threshold_percent=95.0,
                status="ACTIVE",
                created_at=_ts(-30),
                updated_at=_ts(-30),
            )
        )

    # Per-workload daily budgets (first plant only for brevity; others can be added)
    for wl_type, _, _, _, _ in workload_specs:
        wl_id = f"wl-plant-pune-{wl_type}"
        session.add(
            Budget(
                id=f"budget-wl-pune-{wl_type}-daily",
                tenant_id=DEMO_TENANT_ID,
                scope_type="WORKLOAD",
                scope_id=wl_id,
                amount=50.0,         # SIMULATED
                currency="USD",
                period="DAILY",
                warning_threshold_percent=75.0,
                critical_threshold_percent=90.0,
                status="ACTIVE",
                created_at=_ts(-30),
                updated_at=_ts(-30),
            )
        )

    # ------------------------------------------------------------------ routing policies
    #
    # One policy per workload_type × complexity combination for the demo tenant.
    # version=1, status=ACTIVE. All quality/latency scores are SIMULATED.
    #
    complexity_model: dict[tuple[str, str], str] = {
        ("quality_check", "simple"): MODEL_VISION_PHI,
        ("quality_check", "medium"): MODEL_VISION_LLAMA,
        ("quality_check", "complex"): MODEL_VISION_LLAMA,
        ("predictive_maintenance", "simple"): MODEL_VISION_PHI,
        ("predictive_maintenance", "medium"): MODEL_VISION_LLAMA,
        ("predictive_maintenance", "complex"): MODEL_VISION_LLAMA,
        ("supply_chain", "simple"): MODEL_VISION_PHI,
        ("supply_chain", "medium"): MODEL_VISION_LLAMA,
        ("supply_chain", "complex"): MODEL_VISION_LLAMA,
    }
    for (wl_type, complexity), model_id in complexity_model.items():
        policy_id = f"policy-{wl_type}-{complexity}-v1"
        session.add(
            RoutingPolicy(
                id=policy_id,
                tenant_id=DEMO_TENANT_ID,
                workload_type=wl_type,
                complexity=complexity,
                business_priority="NORMAL",
                selected_model_id=model_id,
                selected_agent_id=None,
                max_context_tokens=4096,
                max_tool_calls=5,
                max_cost_per_request=1.0,       # SIMULATED
                max_total_tokens_per_request=2048,
                minimum_quality_score=0.85,     # SIMULATED
                risk_level="LOW",
                version=1,
                status="ACTIVE",
                canary_traffic_percent=None,
                reason="Initial SIMULATED demo policy",
                created_by=admin_user_id,
                approved_by=None,
                created_at=_ts(-20),
                activated_at=_ts(-20),
            )
        )

    # ------------------------------------------------------------------ sample usage + cost events
    # A handful of SIMULATED usage events for the dashboard to display.
    for i, (wl_type, _, _, _, _) in enumerate(workload_specs):
        for day_offset in range(-7, 0):
            event_id = f"demo-usage-{wl_type}-day{day_offset}-{i}"
            usage = UsageEvent(
                id=event_id,
                request_id=f"req-{event_id}",
                trace_id=f"trace-{event_id}",
                tenant_id=DEMO_TENANT_ID,
                user_id=admin_user_id,
                plant_id="plant-pune",
                department_id=f"dept-plant-pune-{wl_type.split('_')[0]}",
                workload_id=f"wl-plant-pune-{wl_type}",
                agent_id=f"agent-plant-pune-{wl_type}",
                model_id=MODEL_VISION_LLAMA,
                timestamp=_ts(day_offset),
                input_tokens=512,           # SIMULATED
                output_tokens=128,          # SIMULATED
                total_tokens=640,           # SIMULATED
                context_tokens=256,         # SIMULATED
                image_count=1 if wl_type == "quality_check" else 0,
                tool_calls=0,
                execution_time_ms=1200,     # SIMULATED
                model_latency_ms=800,       # SIMULATED
                status="SUCCESS",
                error_code=None,
                retry_count=0,
                fallback_used=False,
                quality_score=0.92,         # SIMULATED
                business_priority="NORMAL",
                risk_level="LOW",
                routing_policy_version=1,
                budget_decision="ALLOW",
                guardrail_decision="ALLOW",
                created_at=_ts(day_offset),
            )
            session.add(usage)
            # Paired cost event — provenance=ESTIMATED (not actual billing data)
            session.add(
                CostEvent(
                    id=f"demo-cost-{wl_type}-day{day_offset}-{i}",
                    usage_event_id=event_id,
                    estimated_cost=0.012,   # ESTIMATED — not real billing
                    actual_cost=None,       # unknown; do not fabricate
                    currency="USD",
                    provenance="ESTIMATED",
                    input_cost=0.008,       # ESTIMATED
                    output_cost=0.004,      # ESTIMATED
                    tool_cost=None,
                    infrastructure_cost=None,
                    created_at=_ts(day_offset),
                )
            )

    # ------------------------------------------------------------------ sample forecast
    # forecast_model_name identifies the algorithm, not an LLM (SCHEMA.md §16 note).
    for day_offset in range(1, 8):
        session.add(
            Forecast(
                id=f"demo-forecast-tenant-day{day_offset}",
                tenant_id=DEMO_TENANT_ID,
                scope_type="TENANT",
                scope_id=DEMO_TENANT_ID,
                forecast_date=_ts(day_offset).date(),
                predicted_cost=85.0 + day_offset * 3.5,    # FORECAST
                lower_bound=70.0 + day_offset * 2.0,        # FORECAST
                upper_bound=100.0 + day_offset * 5.0,       # FORECAST
                confidence=0.78,                            # FORECAST
                forecast_model_name="linear_regression_v1",
                forecast_model_version="1.0",
                created_at=_ts(),
            )
        )

    # ------------------------------------------------------------------ sample optimization rec
    session.add(
        OptimizationRecommendation(
            id="demo-opt-rec-001",
            tenant_id=DEMO_TENANT_ID,
            workload_id="wl-plant-pune-quality_check",
            current_strategy="Use Llama-90B-Vision for all quality checks",
            recommended_strategy="Route simple checks to Phi-3.5-vision; keep Llama for complex",
            estimated_saving=120.0,        # ESTIMATED — not realised savings
            estimated_saving_percent=18.5, # ESTIMATED — not realised savings
            quality_impact_percent=-0.5,   # ESTIMATED
            latency_impact_percent=15.0,   # ESTIMATED
            risk_level="LOW",
            recommendation_reason=(
                "SIMULATED DEMO: Phi-3.5 handles simple defect patterns at lower cost. "
                "Complex / safety-critical checks remain on the high-capability model."
            ),
            status="DRAFT",
            applied_policy_id=None,
            superseded_policy_id=None,
            created_at=_ts(-1),
        )
    )

    # ------------------------------------------------------------------ sample audit event
    session.add(
        AuditEvent(
            id="demo-audit-001",
            timestamp=_ts(-30),
            request_id="req-seed-001",
            trace_id=None,
            tenant_id=DEMO_TENANT_ID,
            user_id=admin_user_id,
            action="SEED_DEMO_DATA",
            resource_type="tenant",
            resource_id=DEMO_TENANT_ID,
            before_state=None,
            after_state=json.dumps({"status": "seeded", "note": "SIMULATED DEMO DATA"}),
            reason="Initial demo seed — all data is SIMULATED",
            approval_id=None,
            ip_address=None,
            user_agent="seed_script/1.0",
        )
    )

    await session.flush()
    logger.info("seed_complete", extra={"tenant_id": DEMO_TENANT_ID})
