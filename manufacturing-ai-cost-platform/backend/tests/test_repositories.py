"""Tests: Repository CRUD and tenant isolation.

Verifies:
  - CRUD round-trips for every repository class.
  - Tenant filter: list_by_tenant / list_by_plant etc. returns only records
    belonging to the queried tenant, not records from another tenant.
  - Pagination (limit/offset) works correctly.

All tests run against in-memory SQLite created from the Alembic migrations.
No live services or LLM calls are made (AI_DEVELOPMENT_RULES.md section 25).
"""

from __future__ import annotations

import os

# JWT_SECRET and GENAI_API_KEY must be set before any import of get_settings().
# DATABASE_URL is set per-fixture, not here, to avoid cross-test contamination.
os.environ.setdefault("JWT_SECRET", "test-repos-secret-not-a-credential")
os.environ.setdefault("GENAI_API_KEY", "test-placeholder")

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.db.models.audit import AuditEvent, ModelRegistryHistory
from app.db.models.control_plane import (
    Agent,
    Department,
    Plant,
    Role,
    Tenant,
    User,
    Workload,
)
from app.db.models.governance import Approval, Budget, RoutingPolicy
from app.db.models.intelligence import Anomaly, Forecast, OptimizationRecommendation
from app.db.models.telemetry import CostEvent, UsageEvent
from app.repositories.audit_repository import AuditEventRepository, ModelRegistryHistoryRepository
from app.repositories.budget_repository import BudgetRepository
from app.repositories.forecast_repository import AnomalyRepository, ForecastRepository
from app.repositories.optimization_repository import (
    ApprovalRepository,
    OptimizationRecommendationRepository,
)
from app.repositories.plant_repository import DepartmentRepository, PlantRepository
from app.repositories.routing_policy_repository import RoutingPolicyRepository
from app.repositories.telemetry_repository import CostEventRepository, UsageEventRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import RoleRepository, UserRepository
from app.repositories.workload_repository import AgentRepository, WorkloadRepository

BACKEND_DIR = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Module-scoped migrated database
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def migrated_async_url(tmp_path_factory) -> str:
    tmp = tmp_path_factory.mktemp("repo_test")
    db_path = tmp / "test_repos.db"
    async_url = f"sqlite+aiosqlite:///{db_path}"

    # env.py overwrites sqlalchemy.url from settings.database_url,
    # so we must set the DATABASE_URL env var to our test DB URL.
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = async_url
    from app.core.config import get_settings
    get_settings.cache_clear()

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("path_separator", "os")
    command.upgrade(cfg, "head")

    # Restore env
    if old_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old_url
    get_settings.cache_clear()

    return async_url


@pytest_asyncio.fixture(scope="function")
async def session(migrated_async_url: str) -> AsyncSession:
    engine = create_async_engine(migrated_async_url, connect_args={"timeout": 5})

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(conn, _rec):  # type: ignore[no-untyped-def]
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()

    await engine.dispose()


# ---------------------------------------------------------------------------
# Shared fixture — minimal tenant + plant + department + workload hierarchy
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def hierarchy(session: AsyncSession):
    """Insert and return a complete hierarchy for use in other tests."""
    now = _now()
    t = Tenant(id=_id(), name="Test Co", status="ACTIVE", created_at=now, updated_at=now)
    p = Plant(
        id=_id(), tenant_id=t.id, name="Plant A", location="X",
        timezone="UTC", status="ACTIVE", created_at=now, updated_at=now,
    )
    d = Department(id=_id(), plant_id=p.id, name="Quality", status="ACTIVE")
    wl = Workload(
        id=_id(), plant_id=p.id, department_id=d.id,
        name="QC WL", workload_type="quality_check",
        business_priority="NORMAL", risk_level="LOW", status="ACTIVE",
        created_at=now, updated_at=now,
    )
    ag = Agent(
        id=_id(), workload_id=wl.id, name="QCAgent", agent_type="vision_inspector",
        status="ACTIVE", created_at=now, updated_at=now,
    )
    session.add_all([t, p, d, wl, ag])
    await session.flush()
    return {"tenant": t, "plant": p, "dept": d, "workload": wl, "agent": ag}


# ---------------------------------------------------------------------------
# Tenant repository
# ---------------------------------------------------------------------------

async def test_tenant_crud(session: AsyncSession) -> None:
    repo = TenantRepository(session)
    now = _now()
    t = Tenant(id=_id(), name="CRUD Tenant", status="ACTIVE", created_at=now, updated_at=now)

    added = await repo.add(t)
    assert added.id == t.id

    fetched = await repo.get_by_id(t.id)
    assert fetched is not None
    assert fetched.name == "CRUD Tenant"

    all_tenants = await repo.list_all()
    assert any(x.id == t.id for x in all_tenants)


# ---------------------------------------------------------------------------
# User repository — tenant isolation
# ---------------------------------------------------------------------------

async def test_user_tenant_isolation(session: AsyncSession, hierarchy) -> None:
    """Users from tenant B must not be visible in tenant A queries."""
    now = _now()
    tenant_a = hierarchy["tenant"]

    tenant_b = Tenant(id=_id(), name="Tenant B", status="ACTIVE", created_at=now, updated_at=now)
    session.add(tenant_b)
    await session.flush()

    user_a = User(
        id=_id(), tenant_id=tenant_a.id, username=f"user-a-{_id()}",
        status="ACTIVE", created_at=now, updated_at=now,
    )
    user_b = User(
        id=_id(), tenant_id=tenant_b.id, username=f"user-b-{_id()}",
        status="ACTIVE", created_at=now, updated_at=now,
    )
    session.add_all([user_a, user_b])
    await session.flush()

    repo = UserRepository(session)
    users_a = await repo.list_by_tenant(tenant_a.id)
    user_ids_a = {u.id for u in users_a}

    assert user_a.id in user_ids_a
    assert user_b.id not in user_ids_a, "Cross-tenant user leak!"


async def test_role_crud(session: AsyncSession) -> None:
    repo = RoleRepository(session)
    role = Role(id=_id(), name=f"TEST_ROLE_{_id()[:8]}", description="test")
    await repo.add(role)

    fetched = await repo.get_by_name(role.name)
    assert fetched is not None
    assert fetched.id == role.id


# ---------------------------------------------------------------------------
# Plant repository — tenant isolation
# ---------------------------------------------------------------------------

async def test_plant_tenant_isolation(session: AsyncSession) -> None:
    now = _now()
    t_a = Tenant(id=_id(), name="Plant-A Co", status="ACTIVE", created_at=now, updated_at=now)
    t_b = Tenant(id=_id(), name="Plant-B Co", status="ACTIVE", created_at=now, updated_at=now)
    session.add_all([t_a, t_b])
    await session.flush()

    p_a = Plant(
        id=_id(), tenant_id=t_a.id, name="Plant A1", location="X",
        timezone="UTC", status="ACTIVE", created_at=now, updated_at=now,
    )
    p_b = Plant(
        id=_id(), tenant_id=t_b.id, name="Plant B1", location="Y",
        timezone="UTC", status="ACTIVE", created_at=now, updated_at=now,
    )
    session.add_all([p_a, p_b])
    await session.flush()

    repo = PlantRepository(session)
    plants_a = await repo.list_by_tenant(t_a.id)
    plant_ids_a = {p.id for p in plants_a}

    assert p_a.id in plant_ids_a
    assert p_b.id not in plant_ids_a, "Cross-tenant plant leak!"


async def test_department_crud(session: AsyncSession, hierarchy) -> None:
    repo = DepartmentRepository(session)
    depts = await repo.list_by_plant(hierarchy["plant"].id)
    assert any(d.id == hierarchy["dept"].id for d in depts)


# ---------------------------------------------------------------------------
# Workload + Agent repositories
# ---------------------------------------------------------------------------

async def test_workload_crud(session: AsyncSession, hierarchy) -> None:
    repo = WorkloadRepository(session)
    fetched = await repo.get_by_id(hierarchy["workload"].id)
    assert fetched is not None
    assert fetched.name == "QC WL"


async def test_agent_crud(session: AsyncSession, hierarchy) -> None:
    repo = AgentRepository(session)
    agents = await repo.list_by_workload(hierarchy["workload"].id)
    assert any(a.id == hierarchy["agent"].id for a in agents)


async def test_workload_list_by_plant(session: AsyncSession, hierarchy) -> None:
    repo = WorkloadRepository(session)
    wls = await repo.list_by_plant(hierarchy["plant"].id)
    assert any(w.id == hierarchy["workload"].id for w in wls)


# ---------------------------------------------------------------------------
# Budget repository — tenant isolation
# ---------------------------------------------------------------------------

async def test_budget_tenant_isolation(session: AsyncSession, hierarchy) -> None:
    now = _now()
    tenant_a = hierarchy["tenant"]
    tenant_b = Tenant(id=_id(), name="Budget-B Co", status="ACTIVE", created_at=now, updated_at=now)
    session.add(tenant_b)
    await session.flush()

    b_a = Budget(
        id=_id(), tenant_id=tenant_a.id, scope_type="TENANT",
        scope_id=tenant_a.id, amount=1000.0, currency="USD",
        period="MONTHLY", warning_threshold_percent=80.0,
        critical_threshold_percent=95.0, status="ACTIVE",
    )
    b_b = Budget(
        id=_id(), tenant_id=tenant_b.id, scope_type="TENANT",
        scope_id=tenant_b.id, amount=2000.0, currency="USD",
        period="MONTHLY", warning_threshold_percent=80.0,
        critical_threshold_percent=95.0, status="ACTIVE",
    )
    session.add_all([b_a, b_b])
    await session.flush()

    repo = BudgetRepository(session)
    budgets_a = await repo.list_by_tenant(tenant_a.id)
    budget_ids_a = {b.id for b in budgets_a}

    assert b_a.id in budget_ids_a
    assert b_b.id not in budget_ids_a, "Cross-tenant budget leak!"


async def test_budget_get_for_scope(session: AsyncSession, hierarchy) -> None:
    tenant = hierarchy["tenant"]
    b = Budget(
        id=_id(), tenant_id=tenant.id, scope_type="WORKLOAD",
        scope_id=hierarchy["workload"].id, amount=50.0, currency="USD",
        period="DAILY", warning_threshold_percent=75.0,
        critical_threshold_percent=90.0, status="ACTIVE",
    )
    session.add(b)
    await session.flush()

    repo = BudgetRepository(session)
    found = await repo.get_for_scope(tenant.id, "WORKLOAD", hierarchy["workload"].id)
    assert found is not None
    assert found.id == b.id


# ---------------------------------------------------------------------------
# Routing policy repository
# ---------------------------------------------------------------------------

async def test_routing_policy_crud(session: AsyncSession, hierarchy) -> None:
    tenant = hierarchy["tenant"]
    policy = RoutingPolicy(
        id=_id(), tenant_id=tenant.id,
        workload_type="quality_check", complexity="simple",
        version=1, status="ACTIVE",
    )
    session.add(policy)
    await session.flush()

    repo = RoutingPolicyRepository(session)
    active = await repo.get_active(tenant.id, "quality_check", "simple")
    assert active is not None
    assert active.id == policy.id


async def test_routing_policy_status_update(session: AsyncSession, hierarchy) -> None:
    tenant = hierarchy["tenant"]
    policy = RoutingPolicy(
        id=_id(), tenant_id=tenant.id,
        workload_type="supply_chain", complexity="medium",
        version=1, status="DRAFT",
    )
    session.add(policy)
    await session.flush()

    repo = RoutingPolicyRepository(session)
    updated = await repo.set_status(policy.id, "ACTIVE")
    assert updated is not None
    assert updated.status == "ACTIVE"


# ---------------------------------------------------------------------------
# Telemetry repositories
# ---------------------------------------------------------------------------

async def test_usage_event_crud(session: AsyncSession, hierarchy) -> None:
    now = _now()
    ue = UsageEvent(
        id=_id(), request_id=f"req-{_id()}", timestamp=now,
        tenant_id=hierarchy["tenant"].id, created_at=now,
    )
    session.add(ue)
    await session.flush()

    repo = UsageEventRepository(session)
    fetched = await repo.get_by_id(ue.id)
    assert fetched is not None
    assert fetched.request_id == ue.request_id


async def test_usage_event_tenant_filter(session: AsyncSession, hierarchy) -> None:
    now = _now()
    tenant_a = hierarchy["tenant"]
    tenant_b = Tenant(id=_id(), name="UE-B Co", status="ACTIVE", created_at=now, updated_at=now)
    session.add(tenant_b)
    await session.flush()

    ue_a = UsageEvent(
        id=_id(), request_id=f"req-a-{_id()}", timestamp=now,
        tenant_id=tenant_a.id, created_at=now,
    )
    ue_b = UsageEvent(
        id=_id(), request_id=f"req-b-{_id()}", timestamp=now,
        tenant_id=tenant_b.id, created_at=now,
    )
    session.add_all([ue_a, ue_b])
    await session.flush()

    repo = UsageEventRepository(session)
    events_a = await repo.list_by_tenant(tenant_a.id)
    event_ids_a = {e.id for e in events_a}

    assert ue_a.id in event_ids_a
    assert ue_b.id not in event_ids_a, "Cross-tenant usage event leak!"


async def test_cost_event_crud(session: AsyncSession, hierarchy) -> None:
    now = _now()
    ue = UsageEvent(
        id=_id(), request_id=f"req-cost-{_id()}", timestamp=now,
        tenant_id=hierarchy["tenant"].id, created_at=now,
    )
    session.add(ue)
    await session.flush()

    ce = CostEvent(
        id=_id(), usage_event_id=ue.id, currency="USD",
        provenance="ESTIMATED", estimated_cost=0.01, created_at=now,
    )
    session.add(ce)
    await session.flush()

    repo = CostEventRepository(session)
    fetched = await repo.get_by_usage_event(ue.id)
    assert fetched is not None
    assert fetched.provenance == "ESTIMATED"


# ---------------------------------------------------------------------------
# Forecast + Anomaly repositories
# ---------------------------------------------------------------------------

async def test_forecast_crud(session: AsyncSession, hierarchy) -> None:
    tenant = hierarchy["tenant"]
    f = Forecast(
        id=_id(), tenant_id=tenant.id, scope_type="TENANT",
        scope_id=tenant.id, predicted_cost=100.0,
        confidence=0.8, forecast_model_name="linear_v1",
    )
    session.add(f)
    await session.flush()

    repo = ForecastRepository(session)
    forecasts = await repo.list_by_tenant(tenant.id, scope_type="TENANT")
    assert any(fc.id == f.id for fc in forecasts)


async def test_anomaly_crud(session: AsyncSession, hierarchy) -> None:
    tenant = hierarchy["tenant"]
    a = Anomaly(
        id=_id(), tenant_id=tenant.id, anomaly_type="cost_spike",
        severity="HIGH", expected_value=10.0, actual_value=50.0,
        deviation_percent=400.0, status="OPEN",
    )
    session.add(a)
    await session.flush()

    repo = AnomalyRepository(session)
    anomalies = await repo.list_by_tenant(tenant.id, status="OPEN")
    assert any(an.id == a.id for an in anomalies)


# ---------------------------------------------------------------------------
# Optimization + Approval repositories
# ---------------------------------------------------------------------------

async def test_optimization_recommendation_crud(session: AsyncSession, hierarchy) -> None:
    tenant = hierarchy["tenant"]
    rec = OptimizationRecommendation(
        id=_id(), tenant_id=tenant.id, workload_id=hierarchy["workload"].id,
        estimated_saving=50.0,   # ESTIMATED
        estimated_saving_percent=15.0,  # ESTIMATED
        risk_level="LOW", status="DRAFT",
    )
    session.add(rec)
    await session.flush()

    repo = OptimizationRecommendationRepository(session)
    fetched = await repo.get_by_id(rec.id, tenant.id)
    assert fetched is not None
    assert fetched.status == "DRAFT"


async def test_approval_crud(session: AsyncSession, hierarchy) -> None:
    tenant = hierarchy["tenant"]
    approval = Approval(
        id=_id(), tenant_id=tenant.id, resource_type="routing_policy",
        resource_id=_id(), action="ACTIVATE", risk_level="LOW", status="PENDING",
    )
    session.add(approval)
    await session.flush()

    repo = ApprovalRepository(session)
    fetched = await repo.get_by_id(approval.id, tenant.id)
    assert fetched is not None
    assert fetched.status == "PENDING"

    decided = await repo.decide(approval.id, "APPROVED", "human-reviewer")
    assert decided is not None
    assert decided.status == "APPROVED"
    assert decided.approved_by == "human-reviewer"


async def test_approval_tenant_isolation(session: AsyncSession, hierarchy) -> None:
    now = _now()
    tenant_a = hierarchy["tenant"]
    tenant_b = Tenant(id=_id(), name="Appr-B", status="ACTIVE", created_at=now, updated_at=now)
    session.add(tenant_b)
    await session.flush()

    ap_a = Approval(
        id=_id(), tenant_id=tenant_a.id, resource_type="recommendation",
        resource_id=_id(), action="APPLY", status="PENDING",
    )
    ap_b = Approval(
        id=_id(), tenant_id=tenant_b.id, resource_type="recommendation",
        resource_id=_id(), action="APPLY", status="PENDING",
    )
    session.add_all([ap_a, ap_b])
    await session.flush()

    repo = ApprovalRepository(session)
    approvals_a = await repo.list_by_tenant(tenant_a.id)
    ids_a = {x.id for x in approvals_a}

    assert ap_a.id in ids_a
    assert ap_b.id not in ids_a, "Cross-tenant approval leak!"


# ---------------------------------------------------------------------------
# Audit repositories
# ---------------------------------------------------------------------------

async def test_audit_event_crud(session: AsyncSession, hierarchy) -> None:
    tenant = hierarchy["tenant"]
    evt = AuditEvent(
        id=_id(), timestamp=_now(), tenant_id=tenant.id,
        action="TEST_ACTION", resource_type="test", resource_id=_id(),
    )
    session.add(evt)
    await session.flush()

    repo = AuditEventRepository(session)
    events = await repo.list_by_tenant(tenant.id, action="TEST_ACTION")
    assert any(e.id == evt.id for e in events)


async def test_model_registry_history_crud(session: AsyncSession) -> None:
    entry = ModelRegistryHistory(
        id=_id(), model_id="model-xyz", change_type="PRICE_UPDATE",
        old_value="0.01", new_value="0.008", changed_by="admin",
    )
    session.add(entry)
    await session.flush()

    repo = ModelRegistryHistoryRepository(session)
    history = await repo.list_by_model("model-xyz")
    assert any(h.id == entry.id for h in history)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

async def test_list_by_tenant_pagination(session: AsyncSession) -> None:
    """limit and offset work correctly on at least one repository."""
    now = _now()
    t = Tenant(id=_id(), name="Paged Co", status="ACTIVE", created_at=now, updated_at=now)
    session.add(t)
    await session.flush()

    # Insert 5 plants for the same tenant.
    for i in range(5):
        session.add(
            Plant(
                id=_id(), tenant_id=t.id, name=f"Plant {i}", location="X",
                timezone="UTC", status="ACTIVE", created_at=now, updated_at=now,
            )
        )
    await session.flush()

    repo = PlantRepository(session)
    page1 = await repo.list_by_tenant(t.id, limit=3, offset=0)
    page2 = await repo.list_by_tenant(t.id, limit=3, offset=3)

    assert len(page1) == 3
    assert len(page2) == 2
    # Pages must not overlap.
    assert not {p.id for p in page1}.intersection({p.id for p in page2})
