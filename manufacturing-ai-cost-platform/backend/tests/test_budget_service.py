"""Budget evaluation service tests.

Covers the required cases end to end against real SQL: threshold behaviour,
multiple scopes, precedence, exceeded budget, estimated vs actual, date-range
(period) aggregation, and determinism.

Where ``test_budget_policy.py`` proves the decision rules in isolation, this
proves the wiring: the right budgets are loaded, the right spend is measured
against them over the right period window, and the currency policy is honoured.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.control_plane import Plant, Tenant
from app.db.models.governance import Budget
from app.db.models.telemetry import CostEvent, UsageEvent
from app.policies.budget_policy import BudgetScope, BudgetState, PolicyOutcome
from app.repositories.budget_repository import BudgetRepository
from app.repositories.cost_repository import CostAggregationRepository
from app.services.budget_service import (
    BudgetService,
    RequestContext,
    RequestLimit,
    period_window,
)
from app.services.currency import CurrencyConverter

BASE_CURRENCY = "USD"
PLANT_1 = "plant-budget-1"
PLANT_2 = "plant-budget-2"
DEPT_1 = "dept-budget-1"
WORKLOAD_1 = "workload-budget-1"
MODEL_1 = "model-budget-1"

#: Fixed reference instant. Every window and percentage below is computed from
#: this, never from the wall clock, so the assertions cannot drift.
NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _id() -> str:
    return str(uuid.uuid4())


@pytest_asyncio.fixture
async def tenant_id(db_session: AsyncSession) -> AsyncIterator[str]:
    """A tenant and plant, because budgets carry an FK to tenants."""
    tenant = Tenant(
        id=_id(), name="Budget Co", status="ACTIVE", created_at=NOW, updated_at=NOW
    )
    db_session.add(tenant)
    db_session.add(
        Plant(
            id=PLANT_1,
            tenant_id=tenant.id,
            name="Plant One",
            location="X",
            timezone="UTC",
            status="ACTIVE",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await db_session.flush()
    yield tenant.id


async def _spend(
    session: AsyncSession,
    tenant_id: str,
    *,
    amount: float,
    provenance: str = "ACTUAL",
    when: datetime | None = None,
    plant_id: str | None = PLANT_1,
    department_id: str | None = DEPT_1,
    workload_id: str | None = WORKLOAD_1,
    model_id: str | None = MODEL_1,
) -> None:
    """Record one execution's cost."""
    timestamp = (when or NOW).replace(tzinfo=None)
    usage_id = _id()
    session.add(
        UsageEvent(
            id=usage_id,
            request_id=_id(),
            tenant_id=tenant_id,
            plant_id=plant_id,
            department_id=department_id,
            workload_id=workload_id,
            model_id=model_id,
            timestamp=timestamp,
            total_tokens=100,
            status="SUCCESS",
            created_at=timestamp,
        )
    )
    session.add(
        CostEvent(
            id=_id(),
            usage_event_id=usage_id,
            actual_cost=amount if provenance == "ACTUAL" else None,
            estimated_cost=amount if provenance == "ESTIMATED" else None,
            currency=BASE_CURRENCY,
            provenance=provenance,
            created_at=timestamp,
        )
    )
    await session.flush()


async def _budget(
    session: AsyncSession,
    tenant_id: str,
    *,
    scope_type: str,
    scope_id: str,
    amount: float,
    currency: str = BASE_CURRENCY,
    period: str = "MONTHLY",
    warning: float = 80.0,
    critical: float = 95.0,
) -> Budget:
    budget = Budget(
        id=_id(),
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
        amount=amount,
        currency=currency,
        period=period,
        warning_threshold_percent=warning,
        critical_threshold_percent=critical,
        status="ACTIVE",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(budget)
    await session.flush()
    return budget


def _service(
    session: AsyncSession, *, rates: dict[str, float] | None = None
) -> BudgetService:
    return BudgetService(
        BudgetRepository(session),
        CostAggregationRepository(session),
        converter=CurrencyConverter(base_currency=BASE_CURRENCY, rates=rates or {}),
    )


def _context(tenant_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        plant_id=PLANT_1,
        department_id=DEPT_1,
        workload_id=WORKLOAD_1,
        model_id=MODEL_1,
    )


# ===========================================================================
# Period windows
# ===========================================================================
@pytest.mark.parametrize(
    ("period", "expected_start", "expected_end_day"),
    [
        ("DAILY", datetime(2026, 3, 15, tzinfo=UTC), 15),
        ("MONTHLY", datetime(2026, 3, 1, tzinfo=UTC), 31),
        ("QUARTERLY", datetime(2026, 1, 1, tzinfo=UTC), 31),
        ("ANNUAL", datetime(2026, 1, 1, tzinfo=UTC), 31),
    ],
)
def test_period_windows(
    period: str, expected_start: datetime, expected_end_day: int
) -> None:
    window = period_window(period, NOW)
    assert window.start == expected_start
    assert window.end.day == expected_end_day
    assert window.start < window.end


def test_february_in_a_leap_year_ends_on_the_29th() -> None:
    """Month lengths come from the calendar, not from a table of 30s."""
    window = period_window("MONTHLY", datetime(2024, 2, 10, tzinfo=UTC))
    assert window.end.day == 29


def test_quarter_boundaries() -> None:
    assert period_window("QUARTERLY", datetime(2026, 4, 1, tzinfo=UTC)).start == datetime(
        2026, 4, 1, tzinfo=UTC
    )
    assert period_window("QUARTERLY", datetime(2026, 12, 31, tzinfo=UTC)).start == datetime(
        2026, 10, 1, tzinfo=UTC
    )


def test_a_naive_datetime_is_read_as_utc() -> None:
    """Budgets are enterprise-wide, so the window must not shift with the
    caller's timezone."""
    window = period_window("DAILY", datetime(2026, 3, 15, 12, 0))
    assert window.start == datetime(2026, 3, 15, tzinfo=UTC)


def test_an_unknown_period_fails_loudly() -> None:
    """A CHECK constraint guards the column, so reaching this means the
    constraint and this function have drifted apart."""
    with pytest.raises(ValueError, match="Unrecognised budget period"):
        period_window("FORTNIGHTLY", NOW)


# ===========================================================================
# Threshold behaviour against real spend
# ===========================================================================
async def test_a_budget_in_normal_state_allows(
    db_session: AsyncSession, tenant_id: str
) -> None:
    await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=100.0
    )
    await _spend(db_session, tenant_id, amount=10.0)

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.deciding is not None
    assert decision.deciding.state is BudgetState.NORMAL


async def test_crossing_the_warning_threshold(
    db_session: AsyncSession, tenant_id: str
) -> None:
    await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=100.0
    )
    await _spend(db_session, tenant_id, amount=80.0)

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.deciding is not None
    assert decision.deciding.state is BudgetState.WARNING
    assert decision.outcome is PolicyOutcome.ALLOW


async def test_crossing_the_critical_threshold_downgrades(
    db_session: AsyncSession, tenant_id: str
) -> None:
    await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=100.0
    )
    await _spend(db_session, tenant_id, amount=96.0)

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)
    assert decision.outcome is PolicyOutcome.DOWNGRADE


async def test_an_exceeded_budget_blocks(
    db_session: AsyncSession, tenant_id: str
) -> None:
    await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=100.0
    )
    await _spend(db_session, tenant_id, amount=100.0)

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.outcome is PolicyOutcome.BLOCK
    assert decision.deciding is not None
    assert decision.deciding.state is BudgetState.EXCEEDED


# ===========================================================================
# Estimated vs actual
# ===========================================================================
async def test_estimated_spend_consumes_budget(
    db_session: AsyncSession, tenant_id: str
) -> None:
    """A budget that ignored estimates would report headroom that is not there."""
    await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=100.0
    )
    await _spend(db_session, tenant_id, amount=60.0, provenance="ESTIMATED")
    await _spend(db_session, tenant_id, amount=45.0, provenance="ACTUAL")

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.deciding is not None
    assert decision.deciding.consumed_actual_cost == pytest.approx(45.0)
    assert decision.deciding.consumed_estimated_cost == pytest.approx(60.0)
    assert decision.outcome is PolicyOutcome.BLOCK


async def test_an_unavailable_cost_event_consumes_nothing(
    db_session: AsyncSession, tenant_id: str
) -> None:
    """Unknown is not zero, but it is also not a number that can be charged.

    The execution is visible in telemetry as an UNAVAILABLE event; it cannot
    move a budget it has no figure for.
    """
    await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=100.0
    )
    await _spend(db_session, tenant_id, amount=0.0, provenance="UNAVAILABLE")

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.deciding is not None
    assert decision.deciding.consumed_percent == 0.0


# ===========================================================================
# Period aggregation
# ===========================================================================
async def test_spend_outside_the_period_does_not_count(
    db_session: AsyncSession, tenant_id: str
) -> None:
    """A monthly budget resets with the calendar month."""
    await _budget(
        db_session,
        tenant_id,
        scope_type="TENANT",
        scope_id=tenant_id,
        amount=100.0,
        period="MONTHLY",
    )
    await _spend(
        db_session, tenant_id, amount=500.0, when=datetime(2026, 2, 20, tzinfo=UTC)
    )
    await _spend(db_session, tenant_id, amount=10.0, when=NOW)

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.deciding is not None
    assert decision.deciding.consumed_actual_cost == pytest.approx(10.0)
    assert decision.outcome is PolicyOutcome.ALLOW


async def test_a_daily_budget_sees_only_today(
    db_session: AsyncSession, tenant_id: str
) -> None:
    await _budget(
        db_session,
        tenant_id,
        scope_type="TENANT",
        scope_id=tenant_id,
        amount=100.0,
        period="DAILY",
    )
    await _spend(
        db_session, tenant_id, amount=90.0, when=datetime(2026, 3, 14, 12, 0, tzinfo=UTC)
    )
    await _spend(
        db_session, tenant_id, amount=5.0, when=datetime(2026, 3, 15, 8, 0, tzinfo=UTC)
    )

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.deciding is not None
    assert decision.deciding.consumed_actual_cost == pytest.approx(5.0)


# ===========================================================================
# Multiple scopes and precedence
# ===========================================================================
async def test_every_persisted_scope_is_loaded(
    db_session: AsyncSession, tenant_id: str
) -> None:
    """All seven stored scopes apply to one request when all seven exist."""
    await _budget(
        db_session, tenant_id, scope_type="ENTERPRISE", scope_id=tenant_id, amount=1000.0
    )
    await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=1000.0
    )
    await _budget(
        db_session, tenant_id, scope_type="PLANT", scope_id=PLANT_1, amount=1000.0
    )
    await _budget(
        db_session, tenant_id, scope_type="DEPARTMENT", scope_id=DEPT_1, amount=1000.0
    )
    await _budget(
        db_session, tenant_id, scope_type="WORKLOAD", scope_id=WORKLOAD_1, amount=1000.0
    )
    await _budget(
        db_session, tenant_id, scope_type="AGENT", scope_id="agent-x", amount=1000.0
    )
    await _budget(
        db_session, tenant_id, scope_type="MODEL", scope_id=MODEL_1, amount=1000.0
    )

    context = RequestContext(
        tenant_id=tenant_id,
        plant_id=PLANT_1,
        department_id=DEPT_1,
        workload_id=WORKLOAD_1,
        agent_id="agent-x",
        model_id=MODEL_1,
    )
    decision = await _service(db_session).evaluate(context, now=NOW)

    assert len(decision.evaluations) == 7
    assert {evaluation.scope for evaluation in decision.evaluations} == {
        BudgetScope.ENTERPRISE,
        BudgetScope.TENANT,
        BudgetScope.PLANT,
        BudgetScope.DEPARTMENT,
        BudgetScope.WORKLOAD,
        BudgetScope.AGENT,
        BudgetScope.MODEL,
    }


async def test_the_most_restrictive_scope_decides(
    db_session: AsyncSession, tenant_id: str
) -> None:
    """A roomy tenant budget must not rescue an exhausted plant budget."""
    await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=10_000.0
    )
    await _budget(
        db_session, tenant_id, scope_type="PLANT", scope_id=PLANT_1, amount=50.0
    )
    await _spend(db_session, tenant_id, amount=60.0)

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.outcome is PolicyOutcome.BLOCK
    assert decision.deciding is not None
    assert decision.deciding.scope is BudgetScope.PLANT


async def test_a_budget_for_another_plant_does_not_apply(
    db_session: AsyncSession, tenant_id: str
) -> None:
    await _budget(
        db_session, tenant_id, scope_type="PLANT", scope_id=PLANT_2, amount=1.0
    )
    await _spend(db_session, tenant_id, amount=100.0)

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.evaluations == ()
    assert decision.outcome is PolicyOutcome.ALLOW


async def test_an_inactive_budget_is_not_enforced(
    db_session: AsyncSession, tenant_id: str
) -> None:
    budget = await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=1.0
    )
    budget.status = "INACTIVE"
    await db_session.flush()
    await _spend(db_session, tenant_id, amount=100.0)

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)
    assert decision.outcome is PolicyOutcome.ALLOW


async def test_enterprise_scope_counts_the_whole_tenant(
    db_session: AsyncSession, tenant_id: str
) -> None:
    """ENTERPRISE has no parent entity to derive tenancy from, which is why
    budgets.tenant_id is required even for it (DATABASE_SCHEMA.md section 12)."""
    await _budget(
        db_session, tenant_id, scope_type="ENTERPRISE", scope_id=tenant_id, amount=100.0
    )
    await _spend(db_session, tenant_id, amount=60.0, plant_id=PLANT_1)
    await _spend(db_session, tenant_id, amount=50.0, plant_id=PLANT_2)

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.deciding is not None
    assert decision.deciding.consumed_actual_cost == pytest.approx(110.0)
    assert decision.outcome is PolicyOutcome.BLOCK


# ===========================================================================
# Request-level limits
# ===========================================================================
async def test_a_request_limit_is_enforced_alongside_budgets(
    db_session: AsyncSession, tenant_id: str
) -> None:
    """Not a budgets row — it comes from the routing policy or the request."""
    await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=10_000.0
    )

    decision = await _service(db_session).evaluate(
        _context(tenant_id),
        request_cost=5.0,
        request_limit=RequestLimit(max_cost=1.0, currency=BASE_CURRENCY),
        now=NOW,
    )

    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.deciding is not None
    assert decision.deciding.scope is BudgetScope.REQUEST


async def test_a_request_within_its_limit_passes(
    db_session: AsyncSession, tenant_id: str
) -> None:
    decision = await _service(db_session).evaluate(
        _context(tenant_id),
        request_cost=0.5,
        request_limit=RequestLimit(max_cost=1.0, currency=BASE_CURRENCY),
        now=NOW,
    )
    assert decision.outcome is PolicyOutcome.ALLOW


# ===========================================================================
# Currency
# ===========================================================================
async def test_an_unconvertible_budget_requires_approval(
    db_session: AsyncSession, tenant_id: str
) -> None:
    """No configured rate, so the budget cannot be compared against spend.

    It is neither skipped nor guessed at: a limit nobody can evaluate must not
    silently become permission.
    """
    await _budget(
        db_session,
        tenant_id,
        scope_type="TENANT",
        scope_id=tenant_id,
        amount=100.0,
        currency="INR",
    )

    decision = await _service(db_session).evaluate(_context(tenant_id), now=NOW)

    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.reason == "currency_not_convertible"
    assert decision.deciding is not None
    assert decision.deciding.state is None


async def test_a_configured_rate_makes_the_budget_evaluable(
    db_session: AsyncSession, tenant_id: str
) -> None:
    """8000 INR at 0.0125 is 100 USD, and 60 USD of spend is 60% of it."""
    await _budget(
        db_session,
        tenant_id,
        scope_type="TENANT",
        scope_id=tenant_id,
        amount=8000.0,
        currency="INR",
    )
    await _spend(db_session, tenant_id, amount=60.0)

    service = _service(db_session, rates={"INR": 0.0125})
    decision = await service.evaluate(_context(tenant_id), now=NOW)

    assert decision.deciding is not None
    assert decision.deciding.consumed_percent == pytest.approx(60.0)
    assert decision.outcome is PolicyOutcome.ALLOW


# ===========================================================================
# Status reporting
# ===========================================================================
async def test_status_reports_state_and_consumption(
    db_session: AsyncSession, tenant_id: str
) -> None:
    budget = await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=100.0
    )
    await _spend(db_session, tenant_id, amount=82.0)

    results = await _service(db_session).status_for_budgets([budget], now=NOW)

    assert len(results) == 1
    assert results[0].state == "WARNING"
    assert results[0].consumed_percent == pytest.approx(82.0)
    assert results[0].unevaluable_reason is None


async def test_status_reports_an_unevaluable_budget_as_null_state(
    db_session: AsyncSession, tenant_id: str
) -> None:
    """Null is not a passing state — the reason says why."""
    budget = await _budget(
        db_session,
        tenant_id,
        scope_type="TENANT",
        scope_id=tenant_id,
        amount=100.0,
        currency="INR",
    )
    results = await _service(db_session).status_for_budgets([budget], now=NOW)

    assert results[0].state is None
    assert results[0].unevaluable_reason == "currency_not_convertible"


# ===========================================================================
# Acceptance: determinism
# ===========================================================================
async def test_the_same_data_and_instant_produce_the_same_decision(
    db_session: AsyncSession, tenant_id: str
) -> None:
    await _budget(
        db_session, tenant_id, scope_type="TENANT", scope_id=tenant_id, amount=100.0
    )
    await _budget(
        db_session, tenant_id, scope_type="PLANT", scope_id=PLANT_1, amount=70.0
    )
    await _spend(db_session, tenant_id, amount=33.33)
    await _spend(db_session, tenant_id, amount=11.11, provenance="ESTIMATED")

    service = _service(db_session)
    first = await service.evaluate(_context(tenant_id), request_cost=1.23, now=NOW)
    second = await service.evaluate(_context(tenant_id), request_cost=1.23, now=NOW)

    assert first.outcome is second.outcome
    assert first.deciding == second.deciding
    assert first.evaluations == second.evaluations
