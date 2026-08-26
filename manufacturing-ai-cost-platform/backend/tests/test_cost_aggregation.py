"""Service tests for telemetry persistence and cost aggregation.

Covers:
- aggregation correctness (sums, averages, token volume, counts)
- tenant isolation (never leak events across tenants)
- date range filtering
- actual vs estimated spend distinction (strict provenance isolation)
- empty data handling
- high-volume simulated data (1,000+ executions)

These tests drive ``CostAggregationService`` through an ``AuthorizedScope``,
which is how the production caller uses it. The scope carries the tenant derived
from the authenticated identity; the service has no ``tenant_id`` parameter to
pass, and that is deliberate — a tenant that arrives as a plain argument is a
tenant a caller can choose (SECURITY.md section 5).
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.core.config import Settings
from app.db.base import Base
from app.db.models.control_plane import Department, Plant, Tenant, Workload
from app.db.models.governance import Budget
from app.db.models.telemetry import CostEvent, UsageEvent
from app.main import create_app
from app.repositories.cost_repository import (
    BreakdownDimension,
    CostAggregationRepository,
    Granularity,
)
from app.repositories.telemetry_repository import CostEventRepository, UsageEventRepository
from app.security.scope import AuthorizedScope, ScopeConstraint
from app.services.cost_aggregation import CostAggregationService

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
PLANT_1 = "plant-1"
DEPT_1 = "dept-1"
BASE_CURRENCY = "USD"


def _scope(tenant_id: str = TENANT_A) -> AuthorizedScope:
    """A tenant-wide scope, as an ADMIN or FINOPS_MANAGER would hold."""
    return AuthorizedScope(
        tenant_id=tenant_id, branches=(ScopeConstraint(tenant_id=tenant_id),)
    )


def _service(session) -> CostAggregationService:
    return CostAggregationService(
        CostAggregationRepository(session), base_currency=BASE_CURRENCY
    )


@pytest_asyncio.fixture
async def app_instance(settings: Settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.database.session() as session:
            session.add_all(
                [
                    Tenant(id=TENANT_A, name="Tenant A", status="ACTIVE"),
                    Tenant(id=TENANT_B, name="Tenant B", status="ACTIVE"),
                    Plant(id=PLANT_1, tenant_id=TENANT_A, name="Plant 1", status="ACTIVE"),
                    Department(id=DEPT_1, plant_id=PLANT_1, name="Dept 1", status="ACTIVE"),
                    Workload(
                        id="predictive_maintenance",
                        plant_id=PLANT_1,
                        department_id=DEPT_1,
                        name="PM",
                        workload_type="predictive_maintenance",
                        status="ACTIVE",
                    ),
                    Budget(
                        id="b-001",
                        tenant_id=TENANT_A,
                        scope_type="TENANT",
                        scope_id=TENANT_A,
                        amount=1000.0,
                        status="ACTIVE",
                    ),
                ]
            )
            await session.commit()
        yield app


def _make_telemetry_event(
    tenant_id: str,
    *,
    plant_id: str | None = PLANT_1,
    department_id: str | None = DEPT_1,
    workload_id: str = "predictive_maintenance",
    agent_id: str = "pm-agent",
    model_id: str = "model-a",
    timestamp: datetime | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    actual_cost: float | None = None,
    estimated_cost: float | None = None,
    provenance: str = "ACTUAL",
) -> tuple[UsageEvent, CostEvent]:
    usage = UsageEvent(
        id=str(uuid.uuid4()),
        request_id=f"req-{uuid.uuid4().hex[:8]}",
        trace_id=f"tr-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        plant_id=plant_id,
        department_id=department_id,
        workload_id=workload_id,
        agent_id=agent_id,
        model_id=model_id,
        timestamp=timestamp or datetime.now(UTC),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        execution_time_ms=120,
        status="SUCCESS",
    )
    cost = CostEvent(
        id=str(uuid.uuid4()),
        usage_event_id=usage.id,
        actual_cost=actual_cost,
        estimated_cost=estimated_cost,
        currency=BASE_CURRENCY,
        provenance=provenance,
    )
    usage.cost_event = cost
    return usage, cost


async def _persist(session, events: list[tuple[UsageEvent, CostEvent]]) -> None:
    await UsageEventRepository(session).create_many([u for u, _ in events])
    await CostEventRepository(session).create_many([c for _, c in events])


class TestCostAggregationService:
    """Service-level tests for cost aggregation."""

    async def test_aggregation_correctness(self, app_instance) -> None:
        """Sums, token counts, request counts and the average."""
        async with app_instance.state.database.session() as session:
            await _persist(
                session,
                [
                    _make_telemetry_event(
                        TENANT_A,
                        actual_cost=0.04,
                        provenance="ACTUAL",
                        input_tokens=100,
                        output_tokens=50,
                    ),
                    _make_telemetry_event(
                        TENANT_A,
                        actual_cost=0.06,
                        provenance="ACTUAL",
                        input_tokens=200,
                        output_tokens=100,
                    ),
                    _make_telemetry_event(
                        TENANT_A,
                        estimated_cost=0.02,
                        provenance="ESTIMATED",
                        input_tokens=50,
                        output_tokens=50,
                    ),
                ],
            )

            summary = await _service(session).summary(_scope())

            assert summary.total_requests == 3
            assert summary.total_tokens == 550  # 150 + 300 + 100
            # approx, not ==: 0.04 + 0.06 is 0.10000000000000001 in binary float.
            assert summary.actual_cost == pytest.approx(0.10)
            assert summary.estimated_cost == pytest.approx(0.02)
            assert summary.unavailable_cost_events == 0
            assert summary.average_cost_per_request == pytest.approx(0.12 / 3)
            assert summary.currency == BASE_CURRENCY

    def test_summary_does_not_compute_budget_consumption(self) -> None:
        """Budget percentage is deliberately not the aggregation service's job.

        ``CostSummaryResult`` documents that ``budget_consumed_percent`` and
        ``forecast_month_end_cost`` are supplied by the caller when known.
        Computing a forecast here would present a straight-line guess as the
        platform's forecast, which the intelligence layer owns
        (AI_WORKFLOWS.md section 1).
        """
        from app.services.cost_aggregation import CostSummaryResult

        assert not hasattr(CostSummaryResult, "budget_consumed_percent")
        assert not hasattr(CostSummaryResult, "forecast_month_end_cost")

    async def test_tenant_isolation(self, app_instance) -> None:
        """Tenant A never receives or aggregates tenant B's data."""
        async with app_instance.state.database.session() as session:
            await _persist(
                session,
                [
                    _make_telemetry_event(TENANT_A, actual_cost=10.0, provenance="ACTUAL"),
                    _make_telemetry_event(
                        TENANT_B,
                        plant_id=None,
                        department_id=None,
                        actual_cost=99.0,
                        provenance="ACTUAL",
                    ),
                ],
            )
            service = _service(session)

            summary_a = await service.summary(_scope(TENANT_A))
            summary_b = await service.summary(_scope(TENANT_B))

            assert summary_a.total_requests == 1
            assert summary_a.actual_cost == pytest.approx(10.0)

            assert summary_b.total_requests == 1
            assert summary_b.actual_cost == pytest.approx(99.0)

    async def test_a_scope_cannot_reach_another_tenants_spend(
        self, app_instance
    ) -> None:
        """Isolation comes from the scope, not from a filter the caller passes."""
        async with app_instance.state.database.session() as session:
            await _persist(
                session,
                [
                    _make_telemetry_event(
                        TENANT_B,
                        plant_id=None,
                        department_id=None,
                        actual_cost=99.0,
                        provenance="ACTUAL",
                    )
                ],
            )

            summary = await _service(session).summary(_scope(TENANT_A))

            assert summary.total_requests == 0
            assert summary.actual_cost == pytest.approx(0.0)

    async def test_date_filtering(self, app_instance) -> None:
        """from_ts and to_ts bound the window."""
        now = datetime.now(UTC)
        t1, t2, t3 = now - timedelta(days=5), now - timedelta(days=2), now

        async with app_instance.state.database.session() as session:
            await _persist(
                session,
                [
                    _make_telemetry_event(TENANT_A, timestamp=t1, actual_cost=1.0),
                    _make_telemetry_event(TENANT_A, timestamp=t2, actual_cost=2.0),
                    _make_telemetry_event(TENANT_A, timestamp=t3, actual_cost=3.0),
                ],
            )

            summary = await _service(session).summary(
                _scope(),
                from_ts=t2 - timedelta(hours=1),
                to_ts=t2 + timedelta(hours=1),
            )

            assert summary.total_requests == 1
            assert summary.actual_cost == pytest.approx(2.0)

    async def test_actual_vs_estimated_distinction(self, app_instance) -> None:
        """ACTUAL and ESTIMATED spend stay separate in a breakdown.

        The two are never summed into one unlabelled figure
        (AI_DEVELOPMENT_RULES.md sections 41 and 42).
        """
        async with app_instance.state.database.session() as session:
            await _persist(
                session,
                [
                    _make_telemetry_event(
                        TENANT_A, model_id="model-actual", actual_cost=5.0, provenance="ACTUAL"
                    ),
                    _make_telemetry_event(
                        TENANT_A,
                        model_id="model-estimated",
                        estimated_cost=1.5,
                        provenance="ESTIMATED",
                    ),
                ],
            )

            entries = await _service(session).breakdown(
                _scope(), BreakdownDimension.MODEL
            )
            by_id = {entry.id: entry for entry in entries}

            assert by_id["model-actual"].actual_cost == pytest.approx(5.0)
            assert by_id["model-actual"].estimated_cost == pytest.approx(0.0)

            assert by_id["model-estimated"].actual_cost == pytest.approx(0.0)
            assert by_id["model-estimated"].estimated_cost == pytest.approx(1.5)

    async def test_a_cost_column_is_only_counted_for_its_own_provenance(
        self, app_instance
    ) -> None:
        """An ACTUAL event's estimated_cost column must not be counted as spend.

        Both columns are commonly populated — the estimate made before execution
        and the actual recorded after. Only the one matching the event's
        provenance is real.
        """
        async with app_instance.state.database.session() as session:
            await _persist(
                session,
                [
                    _make_telemetry_event(
                        TENANT_A,
                        actual_cost=7.0,
                        estimated_cost=99.0,
                        provenance="ACTUAL",
                    )
                ],
            )

            summary = await _service(session).summary(_scope())

            assert summary.actual_cost == pytest.approx(7.0)
            assert summary.estimated_cost == pytest.approx(0.0)

    async def test_unavailable_cost_events_are_counted_not_zeroed(
        self, app_instance
    ) -> None:
        """An event whose cost could not be computed is counted, not treated as 0."""
        async with app_instance.state.database.session() as session:
            await _persist(
                session,
                [
                    _make_telemetry_event(TENANT_A, actual_cost=1.0, provenance="ACTUAL"),
                    _make_telemetry_event(TENANT_A, provenance="UNAVAILABLE"),
                ],
            )

            summary = await _service(session).summary(_scope())

            assert summary.total_requests == 2
            assert summary.actual_cost == pytest.approx(1.0)
            assert summary.unavailable_cost_events == 1

    async def test_empty_data_handling(self, app_instance) -> None:
        """Querying an empty tenant returns clean values without error."""
        async with app_instance.state.database.session() as session:
            service = _service(session)
            empty = _scope("empty-tenant")

            summary = await service.summary(empty)
            assert summary.total_requests == 0
            assert summary.total_tokens == 0
            assert summary.actual_cost == pytest.approx(0.0)
            assert summary.estimated_cost == pytest.approx(0.0)
            # None, not 0.0: a period with no traffic has no average, and a zero
            # invites being read as "requests were free".
            assert summary.average_cost_per_request is None

            assert await service.breakdown(empty, BreakdownDimension.MODEL) == []
            assert await service.trend(empty, Granularity.DAY) == []

    async def test_high_volume_simulated_data(self, app_instance) -> None:
        """1,000 simulated executions aggregate to the expected totals."""
        models = ["model-a", "model-b", "model-c"]
        agents = ["pm-agent", "qc-agent", "supply-agent"]

        events: list[tuple[UsageEvent, CostEvent]] = []
        expected_actual = 0.0
        expected_estimated = 0.0
        expected_tokens = 0
        now = datetime.now(UTC)

        # Seeded so a failure is reproducible rather than intermittent.
        # Not cryptographic: this generates fixture volumes and costs.
        rng = random.Random(42)  # noqa: S311
        for index in range(1000):
            is_actual = index % 2 == 0
            cost_value = round(rng.uniform(0.001, 0.05), 4)
            input_tokens = rng.randint(50, 500)
            output_tokens = rng.randint(20, 200)

            events.append(
                _make_telemetry_event(
                    TENANT_A,
                    model_id=rng.choice(models),
                    agent_id=rng.choice(agents),
                    timestamp=now - timedelta(hours=rng.randint(0, 72)),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    actual_cost=cost_value if is_actual else None,
                    estimated_cost=None if is_actual else cost_value,
                    provenance="ACTUAL" if is_actual else "ESTIMATED",
                )
            )

            expected_tokens += input_tokens + output_tokens
            if is_actual:
                expected_actual += cost_value
            else:
                expected_estimated += cost_value

        async with app_instance.state.database.session() as session:
            await _persist(session, events)
            service = _service(session)

            summary = await service.summary(_scope())
            assert summary.total_requests == 1000
            assert summary.total_tokens == expected_tokens
            assert summary.actual_cost == pytest.approx(expected_actual)
            assert summary.estimated_cost == pytest.approx(expected_estimated)

            points = await service.trend(_scope(), Granularity.DAY)
            assert points
            assert sum(point.total_requests for point in points) == 1000
