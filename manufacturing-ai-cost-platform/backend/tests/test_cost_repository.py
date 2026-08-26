"""Cost aggregation repository tests.

Covers the required cases: date range aggregation, estimated vs actual,
unknown pricing (UNAVAILABLE events), multiple scopes, and determinism.

These run against real SQL on a migrated SQLite database. Aggregation, grouping
and date bucketing *are* the behaviour under test, so a stubbed repository would
verify nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.telemetry import CostEvent, UsageEvent
from app.repositories.cost_repository import (
    BreakdownDimension,
    CostAggregationRepository,
    Granularity,
)
from app.security.principal import ResourceScope
from app.security.scope import AuthorizedScope, ScopeConstraint
from app.services.cost_aggregation import CostAggregationService

TENANT_A = "tenant-cost-a"
TENANT_B = "tenant-cost-b"
PLANT_1 = "plant-cost-1"
PLANT_2 = "plant-cost-2"
DEPT_1 = "dept-cost-1"
DEPT_2 = "dept-cost-2"
MODEL_CHEAP = "model-cheap"
MODEL_DEAR = "model-dear"
AGENT_1 = "agent-cost-1"

BASE_CURRENCY = "USD"


def _tenant_scope(tenant_id: str = TENANT_A) -> AuthorizedScope:
    return AuthorizedScope(
        tenant_id=tenant_id, branches=(ScopeConstraint(tenant_id=tenant_id),)
    )


def _plant_scope(plant_id: str, tenant_id: str = TENANT_A) -> AuthorizedScope:
    return AuthorizedScope(
        tenant_id=tenant_id,
        branches=(ScopeConstraint(tenant_id=tenant_id, plant_id=plant_id),),
    )


async def _record(
    session: AsyncSession,
    *,
    tenant_id: str = TENANT_A,
    plant_id: str | None = PLANT_1,
    department_id: str | None = DEPT_1,
    model_id: str | None = MODEL_CHEAP,
    agent_id: str | None = AGENT_1,
    timestamp: datetime,
    actual: float | None = None,
    estimated: float | None = None,
    provenance: str = "ACTUAL",
    total_tokens: int = 100,
) -> None:
    """Insert one usage event and its cost event."""
    usage_id = str(uuid.uuid4())
    session.add(
        UsageEvent(
            id=usage_id,
            request_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            plant_id=plant_id,
            department_id=department_id,
            workload_id=None,
            agent_id=agent_id,
            model_id=model_id,
            timestamp=timestamp,
            total_tokens=total_tokens,
            status="SUCCESS",
            created_at=timestamp,
        )
    )
    session.add(
        CostEvent(
            id=str(uuid.uuid4()),
            usage_event_id=usage_id,
            actual_cost=actual,
            estimated_cost=estimated,
            currency=BASE_CURRENCY,
            provenance=provenance,
            created_at=timestamp,
        )
    )
    await session.flush()


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """A deterministic spread of events across tenants, plants, models and days.

    Tenant A, plant 1:  Jan 10 actual 1.00 (cheap), Jan 10 actual 2.00 (dear)
    Tenant A, plant 1:  Jan 11 estimated 4.00 (cheap)
    Tenant A, plant 1:  Jan 12 UNAVAILABLE (cheap)
    Tenant A, plant 2:  Jan 10 actual 8.00 (cheap)
    Tenant B, plant 1:  Jan 10 actual 1000.00  <- must never appear
    """
    await _record(
        db_session,
        timestamp=datetime(2026, 1, 10, 9, 0, tzinfo=UTC).replace(tzinfo=None),
        actual=1.00,
        model_id=MODEL_CHEAP,
    )
    await _record(
        db_session,
        timestamp=datetime(2026, 1, 10, 15, 0, tzinfo=UTC).replace(tzinfo=None),
        actual=2.00,
        model_id=MODEL_DEAR,
    )
    await _record(
        db_session,
        timestamp=datetime(2026, 1, 11, 9, 0, tzinfo=UTC).replace(tzinfo=None),
        estimated=4.00,
        provenance="ESTIMATED",
    )
    await _record(
        db_session,
        timestamp=datetime(2026, 1, 12, 9, 0, tzinfo=UTC).replace(tzinfo=None),
        provenance="UNAVAILABLE",
    )
    await _record(
        db_session,
        plant_id=PLANT_2,
        department_id=DEPT_2,
        timestamp=datetime(2026, 1, 10, 9, 0, tzinfo=UTC).replace(tzinfo=None),
        actual=8.00,
    )
    await _record(
        db_session,
        tenant_id=TENANT_B,
        timestamp=datetime(2026, 1, 10, 9, 0, tzinfo=UTC).replace(tzinfo=None),
        actual=1000.00,
    )
    yield db_session


@pytest.fixture
def repository(seeded: AsyncSession) -> CostAggregationRepository:
    return CostAggregationRepository(seeded)


# ===========================================================================
# Estimated vs actual vs unavailable
# ===========================================================================
async def test_actual_and_estimated_are_never_blended(
    repository: CostAggregationRepository,
) -> None:
    """The headline reporting rule (AI_DEVELOPMENT_RULES.md sections 41, 42)."""
    totals = await repository.summary(_tenant_scope())

    assert totals.actual_cost == pytest.approx(11.00)  # 1 + 2 + 8
    assert totals.estimated_cost == pytest.approx(4.00)
    # Deliberately not asserting a single 15.00 total: there isn't one.


async def test_unavailable_events_are_counted_not_zeroed(
    repository: CostAggregationRepository,
) -> None:
    """A request whose cost is unknown is not a free request."""
    totals = await repository.summary(_tenant_scope())

    assert totals.unavailable_cost_events == 1
    assert totals.total_requests == 5


async def test_an_unavailable_event_adds_nothing_to_either_total(
    repository: CostAggregationRepository,
) -> None:
    scope = _tenant_scope()
    only_unavailable = await repository.summary(
        scope,
        from_ts=datetime(2026, 1, 12),
        to_ts=datetime(2026, 1, 12, 23, 59, 59),
    )
    assert only_unavailable.actual_cost == 0.0
    assert only_unavailable.estimated_cost == 0.0
    assert only_unavailable.unavailable_cost_events == 1


# ===========================================================================
# Tenant isolation and scope
# ===========================================================================
async def test_another_tenants_spend_is_never_included(
    repository: CostAggregationRepository,
) -> None:
    """Tenant B's 1000.00 must not reach tenant A's total."""
    totals = await repository.summary(_tenant_scope(TENANT_A))
    assert totals.actual_cost == pytest.approx(11.00)
    assert totals.actual_cost < 1000.0


async def test_a_plant_scope_sees_only_that_plant(
    repository: CostAggregationRepository,
) -> None:
    plant_1 = await repository.summary(_plant_scope(PLANT_1))
    plant_2 = await repository.summary(_plant_scope(PLANT_2))

    assert plant_1.actual_cost == pytest.approx(3.00)  # 1 + 2
    assert plant_2.actual_cost == pytest.approx(8.00)


async def test_a_department_scope_sees_only_that_department(
    repository: CostAggregationRepository,
) -> None:
    scope = AuthorizedScope(
        tenant_id=TENANT_A,
        branches=(ScopeConstraint(tenant_id=TENANT_A, department_id=DEPT_2),),
    )
    totals = await repository.summary(scope)
    assert totals.actual_cost == pytest.approx(8.00)


async def test_a_union_scope_covers_both_branches(
    repository: CostAggregationRepository,
) -> None:
    """Plant-1 OR department-2 — the shape a single filter cannot express."""
    scope = AuthorizedScope(
        tenant_id=TENANT_A,
        branches=(
            ScopeConstraint(tenant_id=TENANT_A, plant_id=PLANT_1),
            ScopeConstraint(tenant_id=TENANT_A, department_id=DEPT_2),
        ),
    )
    totals = await repository.summary(scope)
    assert totals.actual_cost == pytest.approx(11.00)


async def test_the_sql_filter_agrees_with_the_authorization_check(
    repository: CostAggregationRepository,
) -> None:
    """The WHERE clause and ``AuthorizedScope.covers`` are two implementations
    of one rule. If they diverge, either rows leak into a total or a caller is
    charged for spend they cannot see."""
    scope = AuthorizedScope(
        tenant_id=TENANT_A,
        branches=(
            ScopeConstraint(tenant_id=TENANT_A, plant_id=PLANT_1),
            ScopeConstraint(tenant_id=TENANT_A, department_id=DEPT_2),
        ),
    )
    rows = [
        (ResourceScope(TENANT_A, PLANT_1, DEPT_1), True),
        (ResourceScope(TENANT_A, PLANT_2, DEPT_2), True),
        (ResourceScope(TENANT_A, PLANT_2, DEPT_1), False),
        (ResourceScope(TENANT_B, PLANT_1, DEPT_1), False),
    ]
    for resource, expected in rows:
        assert scope.covers(resource) is expected, resource

    # And the SQL agrees: four plant-1 events plus the plant-2/dept-2 one.
    # Tenant B's event is excluded, which is the point.
    totals = await repository.summary(scope)
    assert totals.total_requests == 5
    assert totals.actual_cost == pytest.approx(11.00)


# ===========================================================================
# Date range aggregation
# ===========================================================================
async def test_a_date_range_bounds_the_aggregate(
    repository: CostAggregationRepository,
) -> None:
    totals = await repository.summary(
        _tenant_scope(),
        from_ts=datetime(2026, 1, 10),
        to_ts=datetime(2026, 1, 10, 23, 59, 59),
    )
    assert totals.actual_cost == pytest.approx(11.00)
    assert totals.estimated_cost == 0.0


async def test_the_upper_bound_is_inclusive(
    repository: CostAggregationRepository,
) -> None:
    """`to` reads as "up to and including this instant"."""
    totals = await repository.summary(
        _tenant_scope(),
        from_ts=datetime(2026, 1, 11, 9, 0),
        to_ts=datetime(2026, 1, 11, 9, 0),
    )
    assert totals.estimated_cost == pytest.approx(4.00)


async def test_a_range_with_no_events_returns_zeroes_not_nulls(
    repository: CostAggregationRepository,
) -> None:
    """A sum over no rows is genuinely zero; that is not a missing price."""
    totals = await repository.summary(
        _tenant_scope(), from_ts=datetime(2030, 1, 1), to_ts=datetime(2030, 12, 31)
    )
    assert totals.actual_cost == 0.0
    assert totals.total_requests == 0
    assert totals.unavailable_cost_events == 0


async def test_an_open_ended_range_includes_everything(
    repository: CostAggregationRepository,
) -> None:
    bounded = await repository.summary(_tenant_scope(), from_ts=datetime(2026, 1, 1))
    unbounded = await repository.summary(_tenant_scope())
    assert bounded == unbounded


# ===========================================================================
# Breakdowns
# ===========================================================================
async def test_breakdown_by_model(repository: CostAggregationRepository) -> None:
    rows = await repository.breakdown(_tenant_scope(), BreakdownDimension.MODEL)
    by_id = {row.id: row.totals for row in rows}

    assert by_id[MODEL_DEAR].actual_cost == pytest.approx(2.00)
    assert by_id[MODEL_CHEAP].actual_cost == pytest.approx(9.00)  # 1 + 8
    assert by_id[MODEL_CHEAP].estimated_cost == pytest.approx(4.00)


async def test_breakdown_by_plant(repository: CostAggregationRepository) -> None:
    rows = await repository.breakdown(_tenant_scope(), BreakdownDimension.PLANT)
    by_id = {row.id: row.totals for row in rows}

    assert set(by_id) == {PLANT_1, PLANT_2}
    assert by_id[PLANT_2].actual_cost == pytest.approx(8.00)


async def test_breakdown_by_agent(repository: CostAggregationRepository) -> None:
    rows = await repository.breakdown(_tenant_scope(), BreakdownDimension.AGENT)
    assert [row.id for row in rows] == [AGENT_1]


async def test_breakdown_respects_scope(
    repository: CostAggregationRepository,
) -> None:
    rows = await repository.breakdown(_plant_scope(PLANT_2), BreakdownDimension.MODEL)
    assert len(rows) == 1
    assert rows[0].totals.actual_cost == pytest.approx(8.00)


# ===========================================================================
# Trend bucketing
# ===========================================================================
async def test_daily_trend_buckets(repository: CostAggregationRepository) -> None:
    rows = await repository.trend(_tenant_scope(), Granularity.DAY)

    assert [row.bucket_key for row in rows] == [
        "2026-01-10T00:00:00",
        "2026-01-11T00:00:00",
        "2026-01-12T00:00:00",
    ]
    assert rows[0].totals.actual_cost == pytest.approx(11.00)
    assert rows[1].totals.estimated_cost == pytest.approx(4.00)
    assert rows[2].totals.unavailable_cost_events == 1


async def test_hourly_trend_separates_same_day_events(
    repository: CostAggregationRepository,
) -> None:
    rows = await repository.trend(
        _plant_scope(PLANT_1),
        Granularity.HOUR,
        from_ts=datetime(2026, 1, 10),
        to_ts=datetime(2026, 1, 10, 23, 59, 59),
    )
    assert [row.bucket_key for row in rows] == [
        "2026-01-10T09:00:00",
        "2026-01-10T15:00:00",
    ]


async def test_monthly_trend_collapses_the_month(
    repository: CostAggregationRepository,
) -> None:
    rows = await repository.trend(_tenant_scope(), Granularity.MONTH)
    assert len(rows) == 1
    assert rows[0].bucket_key == "2026-01-01T00:00:00"
    assert rows[0].totals.actual_cost == pytest.approx(11.00)


async def test_trend_is_ordered_oldest_first(
    repository: CostAggregationRepository,
) -> None:
    rows = await repository.trend(_tenant_scope(), Granularity.DAY)
    keys = [row.bucket_key for row in rows]
    assert keys == sorted(keys)


def test_postgresql_bucketing_compiles() -> None:
    """The migration path is real, not theoretical.

    The expression is not executed — there is no PostgreSQL here — but it must
    at least compile against that dialect, so a future migration fails on data,
    not on a syntax error nobody ever tried.
    """
    from sqlalchemy import func
    from sqlalchemy.dialects import postgresql

    from app.db.models.telemetry import UsageEvent as Event

    expression = func.to_char(
        func.date_trunc("day", Event.timestamp), 'YYYY-MM-DD"T"HH24:MI:SS'
    )
    compiled = str(expression.compile(dialect=postgresql.dialect()))
    assert "date_trunc" in compiled


# ===========================================================================
# Service layer
# ===========================================================================
async def test_service_reports_the_base_currency(seeded: AsyncSession) -> None:
    service = CostAggregationService(
        CostAggregationRepository(seeded), base_currency=BASE_CURRENCY
    )
    summary = await service.summary(_tenant_scope())
    assert summary.currency == BASE_CURRENCY


async def test_average_cost_per_request_is_none_without_traffic(
    seeded: AsyncSession,
) -> None:
    """A period with no requests has no average. Rendering one as 0.0 invites it
    to be read as "requests were free"."""
    service = CostAggregationService(
        CostAggregationRepository(seeded), base_currency=BASE_CURRENCY
    )
    summary = await service.summary(
        _tenant_scope(), from_ts=datetime(2030, 1, 1), to_ts=datetime(2030, 12, 31)
    )
    assert summary.total_requests == 0
    assert summary.average_cost_per_request is None


async def test_breakdown_names_are_null_when_unresolved(
    seeded: AsyncSession,
) -> None:
    """A row with real spend is never dropped or relabelled just because its
    display name could not be resolved."""
    service = CostAggregationService(
        CostAggregationRepository(seeded), base_currency=BASE_CURRENCY
    )
    entries = await service.breakdown(_tenant_scope(), BreakdownDimension.MODEL)
    assert all(entry.name is None for entry in entries)
    assert all(entry.id is not None for entry in entries)


# ===========================================================================
# Acceptance: determinism
# ===========================================================================
async def test_repeated_queries_return_identical_results(
    repository: CostAggregationRepository,
) -> None:
    first = await repository.summary(_tenant_scope())
    second = await repository.summary(_tenant_scope())
    assert first == second


async def test_breakdown_ordering_is_stable(
    repository: CostAggregationRepository,
) -> None:
    """Ordering is part of the result, not an implementation detail."""
    first = await repository.breakdown(_tenant_scope(), BreakdownDimension.MODEL)
    second = await repository.breakdown(_tenant_scope(), BreakdownDimension.MODEL)
    assert [row.id for row in first] == [row.id for row in second]
    assert first == second
