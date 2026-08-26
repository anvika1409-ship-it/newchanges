"""Budget evaluation service.

Loads the budgets that apply, measures spend against them, and hands the numbers
to the deterministic policy in ``app.policies.budget_policy``. The split matters:
this module does I/O, that one makes decisions, and the decision function stays
pure so it can be exhaustively tested without a database.

Period windows
--------------

A budget's consumption is measured over its *current period*, computed from a
caller-supplied ``now``. The clock is a parameter rather than a call to
``datetime.now`` inside the calculation, which is what lets the acceptance
criterion hold: the same stored events and the same reference instant always
produce the same figures.

Windows are UTC calendar periods, half-open at the end where that matters:

    DAILY      the calendar day containing ``now``
    MONTHLY    the calendar month containing ``now``
    QUARTERLY  the calendar quarter containing ``now``
    ANNUAL     the calendar year containing ``now``

Currency
--------

A budget is compared against spend only when both are in the same currency, or
when the configured conversion policy can bridge them
(DATABASE_SCHEMA.md section 15). When neither holds, the budget is marked
unevaluable and the policy turns that into REQUIRE_APPROVAL. It is never
silently skipped: a limit nobody can evaluate must not become permission.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger
from app.db.models.governance import Budget
from app.policies.budget_policy import (
    DEFAULT_BUDGET_POLICY,
    BudgetDecision,
    BudgetLimit,
    BudgetPolicy,
    BudgetScope,
    decide,
    evaluate_limit,
)
from app.repositories.budget_repository import BudgetRepository
from app.repositories.cost_repository import CostAggregationRepository
from app.services.currency import ConversionUnavailableError, CurrencyConverter

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PeriodWindow:
    """The inclusive-start, inclusive-end instants a budget is measured over."""

    start: datetime
    end: datetime


def period_window(period: str, now: datetime) -> PeriodWindow:
    """The current period for ``period`` containing ``now``.

    ``now`` is normalised to UTC. Budgets are enterprise-wide limits, and
    evaluating one differently depending on which plant's timezone asked would
    make the same budget report two different states at the same instant.

    Raises:
        ValueError: on an unrecognised period. The column has a CHECK
            constraint, so reaching this means the constraint and this function
            have drifted apart, which should fail loudly.
    """
    moment = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)

    match period.upper():
        case "DAILY":
            start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1) - timedelta(microseconds=1)
        case "MONTHLY":
            start = moment.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            last_day = monthrange(start.year, start.month)[1]
            end = start.replace(day=last_day) + timedelta(days=1) - timedelta(microseconds=1)
        case "QUARTERLY":
            first_month = 3 * ((moment.month - 1) // 3) + 1
            start = moment.replace(
                month=first_month, day=1, hour=0, minute=0, second=0, microsecond=0
            )
            end_month = first_month + 2
            last_day = monthrange(start.year, end_month)[1]
            end = (
                start.replace(month=end_month, day=last_day)
                + timedelta(days=1)
                - timedelta(microseconds=1)
            )
        case "ANNUAL":
            start = moment.replace(
                month=1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
            end = start.replace(year=start.year + 1) - timedelta(microseconds=1)
        case _:
            raise ValueError(
                f"Unrecognised budget period {period!r}. Expected one of "
                "DAILY, MONTHLY, QUARTERLY, ANNUAL (DATABASE_SCHEMA.md section 12)."
            )

    return PeriodWindow(start=start, end=end)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The scopes one AI execution belongs to.

    Every non-null field contributes a candidate budget scope. ``None`` means
    the request has no such dimension, and a budget at that scope simply does
    not apply.
    """

    tenant_id: str
    plant_id: str | None = None
    department_id: str | None = None
    workload_id: str | None = None
    agent_id: str | None = None
    model_id: str | None = None

    def scope_pairs(self) -> list[tuple[str, str]]:
        """(scope_type, scope_id) pairs a stored budget could match.

        ENTERPRISE and TENANT both key on the tenant id: DATABASE_SCHEMA.md
        section 12 notes ENTERPRISE has no parent entity to derive tenancy from,
        which is why ``budgets.tenant_id`` is required even for it.
        """
        pairs: list[tuple[str, str]] = [
            (BudgetScope.ENTERPRISE, self.tenant_id),
            (BudgetScope.TENANT, self.tenant_id),
        ]
        optional: list[tuple[BudgetScope, str | None]] = [
            (BudgetScope.PLANT, self.plant_id),
            (BudgetScope.DEPARTMENT, self.department_id),
            (BudgetScope.WORKLOAD, self.workload_id),
            (BudgetScope.AGENT, self.agent_id),
            (BudgetScope.MODEL, self.model_id),
        ]
        pairs.extend((str(scope), value) for scope, value in optional if value)
        return [(str(scope), value) for scope, value in pairs]


@dataclass(frozen=True, slots=True)
class RequestLimit:
    """A per-request cost ceiling.

    Not a ``budgets`` row. DATABASE_SCHEMA.md section 12 and SECURITY.md section
    13 both place request-level limits on
    ``routing_policies.max_cost_per_request`` and ``AIExecutionRequest.max_cost``,
    so this is constructed from those and evaluated alongside the stored budgets
    rather than being persisted as an eighth scope type.
    """

    max_cost: float
    currency: str
    source: str = "routing_policy"


@dataclass(frozen=True, slots=True)
class BudgetStatusResult:
    """One budget's current state, for ``GET /budgets/status``."""

    budget: Budget
    consumed_actual_cost: float
    consumed_estimated_cost: float
    consumed_percent: float
    state: str | None
    unevaluable_reason: str | None


class BudgetService:
    """Budget evaluation and status reporting."""

    def __init__(
        self,
        budgets: BudgetRepository,
        costs: CostAggregationRepository,
        *,
        converter: CurrencyConverter,
        policy: BudgetPolicy = DEFAULT_BUDGET_POLICY,
    ) -> None:
        self._budgets = budgets
        self._costs = costs
        self._converter = converter
        self._policy = policy

    # ------------------------------------------------------------ evaluation
    async def evaluate(
        self,
        context: RequestContext,
        *,
        request_cost: float = 0.0,
        request_limit: RequestLimit | None = None,
        now: datetime | None = None,
    ) -> BudgetDecision:
        """Decide whether a request may proceed under every applicable limit.

        Args:
            context: the scopes the request belongs to.
            request_cost: the request's estimated cost, in the base currency.
                Used for the projected-breach check; pass 0.0 to evaluate
                current state only.
            request_limit: a per-request ceiling from the routing policy or the
                request itself.
            now: reference instant for period windows. Defaults to the current
                time; pass it explicitly for reproducible results.

        Returns:
            The composed decision. The most restrictive applicable outcome wins
            (see ``app.policies.budget_policy``).
        """
        reference = now or datetime.now(UTC)
        limits = await self._load_limits(context, reference=reference)

        if request_limit is not None:
            limits.append(self._request_limit_to_budget_limit(request_limit))

        decision = decide(limits, request_cost=request_cost, policy=self._policy)

        logger.info(
            "budget_evaluated",
            extra={
                "budget_decision": str(decision.outcome),
                "budget_reason": decision.reason,
                "deciding_scope": (
                    str(decision.deciding.scope) if decision.deciding else None
                ),
                "limits_evaluated": len(decision.evaluations),
            },
        )
        return decision

    async def _load_limits(
        self, context: RequestContext, *, reference: datetime
    ) -> list[BudgetLimit]:
        """Fetch applicable budgets and measure spend against each."""
        rows = await self._budgets.list_for_scopes(
            context.tenant_id, context.scope_pairs()
        )
        limits: list[BudgetLimit] = []
        for budget in rows:
            limits.append(await self._to_limit(budget, reference=reference))
        return limits

    async def _to_limit(self, budget: Budget, *, reference: datetime) -> BudgetLimit:
        window = period_window(budget.period, reference)
        spend = await self._costs.spend_for_scope(
            budget.tenant_id,
            budget.scope_type,
            budget.scope_id,
            from_ts=window.start,
            to_ts=window.end,
        )

        # Spend is aggregated in the platform base currency; the budget may be
        # denominated in another. Convert the budget rather than the spend, so
        # one conversion covers the whole comparison.
        amount = budget.amount
        unevaluable: str | None = None
        if not self._converter.can_convert(budget.currency):
            unevaluable = "currency_not_convertible"
        else:
            try:
                amount = self._converter.to_base(budget.amount, budget.currency)
            except ConversionUnavailableError:
                # Guarded by can_convert; kept so a converter change cannot turn
                # a missing rate into a silently wrong comparison.
                unevaluable = "currency_not_convertible"

        return BudgetLimit(
            scope=BudgetScope(budget.scope_type),
            scope_id=budget.scope_id,
            amount=amount,
            currency=self._converter.base_currency,
            consumed_actual_cost=spend.actual_cost,
            consumed_estimated_cost=spend.estimated_cost,
            warning_threshold_percent=budget.warning_threshold_percent,
            critical_threshold_percent=budget.critical_threshold_percent,
            budget_id=budget.id,
            unevaluable_reason=unevaluable,
        )

    def _request_limit_to_budget_limit(self, limit: RequestLimit) -> BudgetLimit:
        """Model a per-request ceiling as a limit with no prior consumption.

        Consumption is zero by definition — a request-level ceiling applies to
        this request alone — so the projected-breach path is what enforces it.
        """
        unevaluable = (
            None
            if self._converter.can_convert(limit.currency)
            else "currency_not_convertible"
        )
        amount = limit.max_cost
        if unevaluable is None:
            amount = self._converter.to_base(limit.max_cost, limit.currency)

        return BudgetLimit(
            scope=BudgetScope.REQUEST,
            scope_id=limit.source,
            amount=amount,
            currency=self._converter.base_currency,
            consumed_actual_cost=0.0,
            consumed_estimated_cost=0.0,
            budget_id=None,
            unevaluable_reason=unevaluable,
        )

    # ---------------------------------------------------------------- status
    async def status_for_budgets(
        self, budgets: list[Budget], *, now: datetime | None = None
    ) -> list[BudgetStatusResult]:
        """Current state of each budget, for ``GET /budgets/status``.

        Ordered exactly as ``budgets`` was passed, so pagination stays stable.
        """
        reference = now or datetime.now(UTC)
        results: list[BudgetStatusResult] = []

        for budget in budgets:
            limit = await self._to_limit(budget, reference=reference)
            evaluation = evaluate_limit(limit, request_cost=0.0, policy=self._policy)
            results.append(
                BudgetStatusResult(
                    budget=budget,
                    consumed_actual_cost=evaluation.consumed_actual_cost,
                    consumed_estimated_cost=evaluation.consumed_estimated_cost,
                    consumed_percent=evaluation.consumed_percent,
                    state=str(evaluation.state) if evaluation.state else None,
                    unevaluable_reason=(
                        None if evaluation.is_evaluable else evaluation.reason
                    ),
                )
            )
        return results
