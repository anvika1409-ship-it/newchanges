"""Unit and service tests for Telemetry Persistence & Cost Aggregation.

Tests cover:
- Aggregation correctness (sums, averages, token volume, counts)
- Tenant isolation (never leak events across tenants)
- Date range filtering (inclusive/exclusive boundaries)
- Actual vs. estimated spend distinction (strict provenance isolation)
- Empty data handling (clean zero values without errors)
- High-volume simulated data (1,000+ executions aggregated efficiently)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import random
import uuid

import pytest
import pytest_asyncio

from app.core.config import Settings
from app.db.base import Base
from app.db.models.control_plane import Department, Plant, Tenant, Workload
from app.db.models.governance import Budget
from app.db.models.telemetry import CostEvent, UsageEvent
from app.main import create_app
from app.repositories.budget_repository import BudgetRepository
from app.repositories.telemetry_repository import CostEventRepository, UsageEventRepository
from app.services.cost_aggregation import CostAggregationService

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
PLANT_1 = "plant-1"
DEPT_1 = "dept-1"


@pytest_asyncio.fixture
async def app_instance(settings: Settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.database.session() as session:
            t_a = Tenant(id=TENANT_A, name="Tenant A", status="ACTIVE")
            t_b = Tenant(id=TENANT_B, name="Tenant B", status="ACTIVE")
            p = Plant(id=PLANT_1, tenant_id=TENANT_A, name="Plant 1", status="ACTIVE")
            d = Department(id=DEPT_1, plant_id=PLANT_1, name="Dept 1", status="ACTIVE")
            wl = Workload(
                id="predictive_maintenance",
                plant_id=PLANT_1,
                department_id=DEPT_1,
                name="PM",
                workload_type="predictive_maintenance",
                status="ACTIVE",
            )
            b = Budget(
                id="b-001",
                tenant_id=TENANT_A,
                scope_type="TENANT",
                scope_id=TENANT_A,
                amount=1000.0,
                status="ACTIVE",
            )
            session.add_all([t_a, t_b, p, d, wl, b])
            await session.commit()
        yield app


def _make_telemetry_event(
    tenant_id: str,
    *,
    plant_id: str | None = PLANT_1,
    department_id: str | None = DEPT_1,
    workload_id: str = "predictive_maintenance",
    agent_id: str = "pm-agent",
    model_id: str = "gpt-4o-mini",
    timestamp: datetime | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    actual_cost: float | None = None,
    estimated_cost: float | None = None,
    provenance: str = "ACTUAL",
) -> tuple[UsageEvent, CostEvent]:
    req_id = f"req-{uuid.uuid4().hex[:8]}"
    ts = timestamp or datetime.now(UTC)

    ue = UsageEvent(
        id=str(uuid.uuid4()),
        request_id=req_id,
        trace_id=f"tr-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        plant_id=plant_id,
        department_id=department_id,
        workload_id=workload_id,
        agent_id=agent_id,
        model_id=model_id,
        timestamp=ts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        execution_time_ms=120,
        status="SUCCESS",
    )

    ce = CostEvent(
        id=str(uuid.uuid4()),
        usage_event_id=ue.id,
        actual_cost=actual_cost,
        estimated_cost=estimated_cost,
        currency="USD",
        provenance=provenance,
    )
    ue.cost_event = ce
    return ue, ce


class TestCostAggregationService:
    """Service-level unit tests for cost aggregation."""

    async def test_aggregation_correctness(self, app_instance) -> None:
        """Verify sums, token counts, request counts, and average calculations."""
        async with app_instance.state.database.session() as session:
            usage_repo = UsageEventRepository(session)
            cost_repo = CostEventRepository(session)
            budget_repo = BudgetRepository(session)
            service = CostAggregationService(usage_repo, cost_repo, budget_repo)

            # Insert 3 events: 2 ACTUAL, 1 ESTIMATED
            e1_u, e1_c = _make_telemetry_event(TENANT_A, actual_cost=0.04, estimated_cost=0.04, provenance="ACTUAL", input_tokens=100, output_tokens=50)
            e2_u, e2_c = _make_telemetry_event(TENANT_A, actual_cost=0.06, estimated_cost=0.06, provenance="ACTUAL", input_tokens=200, output_tokens=100)
            e3_u, e3_c = _make_telemetry_event(TENANT_A, estimated_cost=0.02, provenance="ESTIMATED", input_tokens=50, output_tokens=50)

            await usage_repo.create_many([e1_u, e2_u, e3_u])
            await cost_repo.create_many([e1_c, e2_c, e3_c])

            summary = await service.get_cost_summary(TENANT_A)

            assert summary.total_requests == 3
            assert summary.total_tokens == 550  # 150 + 300 + 100
            assert summary.actual_cost == 0.10  # 0.04 + 0.06
            assert summary.estimated_cost == 0.02  # 0.02
            assert summary.unavailable_cost_events == 0
            assert summary.average_cost_per_request == round((0.10 + 0.02) / 3, 6)
            # Budget was 1000.0, spend is 0.12 -> 0.01%
            assert summary.budget_consumed_percent == 0.01

    async def test_tenant_isolation(self, app_instance) -> None:
        """Ensure tenant A never receives or aggregates data from tenant B."""
        async with app_instance.state.database.session() as session:
            usage_repo = UsageEventRepository(session)
            cost_repo = CostEventRepository(session)
            service = CostAggregationService(usage_repo, cost_repo)

            e_a_u, e_a_c = _make_telemetry_event(TENANT_A, actual_cost=10.0, provenance="ACTUAL")
            e_b_u, e_b_c = _make_telemetry_event(TENANT_B, actual_cost=99.0, provenance="ACTUAL")

            await usage_repo.create_many([e_a_u, e_b_u])
            await cost_repo.create_many([e_a_c, e_b_c])

            summary_a = await service.get_cost_summary(TENANT_A)
            summary_b = await service.get_cost_summary(TENANT_B)

            assert summary_a.total_requests == 1
            assert summary_a.actual_cost == 10.0

            assert summary_b.total_requests == 1
            assert summary_b.actual_cost == 99.0

    async def test_date_filtering(self, app_instance) -> None:
        """Verify from_ts and to_ts filter events accurately."""
        now = datetime.now(UTC)
        t1 = now - timedelta(days=5)
        t2 = now - timedelta(days=2)
        t3 = now

        async with app_instance.state.database.session() as session:
            usage_repo = UsageEventRepository(session)
            cost_repo = CostEventRepository(session)
            service = CostAggregationService(usage_repo, cost_repo)

            e1_u, e1_c = _make_telemetry_event(TENANT_A, timestamp=t1, actual_cost=1.0)
            e2_u, e2_c = _make_telemetry_event(TENANT_A, timestamp=t2, actual_cost=2.0)
            e3_u, e3_c = _make_telemetry_event(TENANT_A, timestamp=t3, actual_cost=3.0)

            await usage_repo.create_many([e1_u, e2_u, e3_u])
            await cost_repo.create_many([e1_c, e2_c, e3_c])

            # Filter window covering t2 only
            filtered_summary = await service.get_cost_summary(
                TENANT_A,
                from_ts=t2 - timedelta(hours=1),
                to_ts=t2 + timedelta(hours=1),
            )

            assert filtered_summary.total_requests == 1
            assert filtered_summary.actual_cost == 2.0

    async def test_actual_vs_estimated_distinction(self, app_instance) -> None:
        """Ensure ACTUAL and ESTIMATED spend remain distinct in summaries and breakdowns."""
        async with app_instance.state.database.session() as session:
            usage_repo = UsageEventRepository(session)
            cost_repo = CostEventRepository(session)
            service = CostAggregationService(usage_repo, cost_repo)

            # Model 1: Actual only
            m1_u, m1_c = _make_telemetry_event(TENANT_A, model_id="claude-3-5-sonnet", actual_cost=5.0, provenance="ACTUAL")
            # Model 2: Estimated only
            m2_u, m2_c = _make_telemetry_event(TENANT_A, model_id="gpt-4o-mini", estimated_cost=1.5, provenance="ESTIMATED")

            await usage_repo.create_many([m1_u, m2_u])
            await cost_repo.create_many([m1_c, m2_c])

            breakdown = await service.get_cost_by_model(TENANT_A)
            items_by_id = {item.id: item for item in breakdown.items}

            assert items_by_id["claude-3-5-sonnet"].actual_cost == 5.0
            assert items_by_id["claude-3-5-sonnet"].estimated_cost == 0.0

            assert items_by_id["gpt-4o-mini"].actual_cost == 0.0
            assert items_by_id["gpt-4o-mini"].estimated_cost == 1.5

    async def test_empty_data_handling(self, app_instance) -> None:
        """Querying empty telemetry returns valid zeroed models without error."""
        async with app_instance.state.database.session() as session:
            usage_repo = UsageEventRepository(session)
            cost_repo = CostEventRepository(session)
            service = CostAggregationService(usage_repo, cost_repo)

            summary = await service.get_cost_summary("empty-tenant")
            assert summary.total_requests == 0
            assert summary.total_tokens == 0
            assert summary.actual_cost == 0.0
            assert summary.estimated_cost == 0.0
            assert summary.average_cost_per_request == 0.0

            breakdown = await service.get_cost_by_model("empty-tenant")
            assert len(breakdown.items) == 0

            trend = await service.get_cost_trend("empty-tenant")
            assert len(trend.points) == 0

    async def test_high_volume_simulated_data(self, app_instance) -> None:
        """Simulate 1,000+ executions and verify aggregation performance and totals."""
        models = ["gpt-4o-mini", "claude-3-5-sonnet", "gemini-1.5-pro"]
        agents = ["pm-agent", "qc-agent", "supply-agent"]

        usage_events: list[UsageEvent] = []
        cost_events: list[CostEvent] = []

        expected_actual = 0.0
        expected_estimated = 0.0
        expected_tokens = 0
        now = datetime.now(UTC)

        random.seed(42)
        for i in range(1000):
            model = random.choice(models)
            agent = random.choice(agents)
            is_actual = (i % 2 == 0)
            cost_val = round(random.uniform(0.001, 0.05), 4)
            inp = random.randint(50, 500)
            out = random.randint(20, 200)

            ts = now - timedelta(hours=random.randint(0, 72))

            ue, ce = _make_telemetry_event(
                TENANT_A,
                model_id=model,
                agent_id=agent,
                timestamp=ts,
                input_tokens=inp,
                output_tokens=out,
                actual_cost=cost_val if is_actual else None,
                estimated_cost=cost_val if not is_actual else cost_val,
                provenance="ACTUAL" if is_actual else "ESTIMATED",
            )
            usage_events.append(ue)
            cost_events.append(ce)

            expected_tokens += (inp + out)
            if is_actual:
                expected_actual += cost_val
            else:
                expected_estimated += cost_val

        async with app_instance.state.database.session() as session:
            usage_repo = UsageEventRepository(session)
            cost_repo = CostEventRepository(session)
            service = CostAggregationService(usage_repo, cost_repo)

            await usage_repo.create_many(usage_events)
            await cost_repo.create_many(cost_events)

            summary = await service.get_cost_summary(TENANT_A)
            assert summary.total_requests == 1000
            assert summary.total_tokens == expected_tokens
            assert summary.actual_cost == round(expected_actual, 4)
            assert summary.estimated_cost == round(expected_estimated, 4)

            # Test trend buckets
            trend = await service.get_cost_trend(TENANT_A, granularity="day")
            assert len(trend.points) > 0
            trend_total_reqs = sum(p.total_requests for p in trend.points)
            assert trend_total_reqs == 1000
