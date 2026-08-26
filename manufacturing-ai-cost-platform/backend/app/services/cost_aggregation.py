"""Cost aggregation service.

Sits between the cost endpoints and ``CostAggregationRepository``. The
repository knows SQL; this service knows the reporting rules:

* actual and estimated spend are reported side by side and never summed into one
  unlabelled figure (AI_DEVELOPMENT_RULES.md sections 41 and 42);
* events whose cost could not be computed are counted, not treated as zero;
* every query is bounded by the caller's ``AuthorizedScope``, so the tenant
  filter comes from the authenticated identity rather than from a parameter
  (SECURITY.md section 5).

Determinism: given the same stored events and the same window, every method here
returns the same values in the same order. Nothing consults a clock except the
default window, which the caller may pass explicitly — and the endpoints do.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.logging import get_logger
from app.repositories.cost_repository import (
    BreakdownDimension,
    CostAggregationRepository,
    CostTotals,
    Granularity,
)
from app.security.scope import AuthorizedScope

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CostSummaryResult:
    """Aggregate spend plus the derived figures the contract's CostSummary wants.

    ``budget_consumed_percent`` and ``forecast_month_end_cost`` are optional in
    the contract and are supplied by the caller when they are known — this
    service does not compute either. Forecasting belongs to the intelligence
    layer (AI_WORKFLOWS.md section 1); producing a number here and labelling it a
    forecast would present a straight-line guess as the platform's forecast.
    """

    actual_cost: float
    estimated_cost: float
    unavailable_cost_events: int
    currency: str
    total_requests: int
    total_tokens: int
    average_cost_per_request: float | None

    @classmethod
    def from_totals(cls, totals: CostTotals, *, currency: str) -> CostSummaryResult:
        return cls(
            actual_cost=totals.actual_cost,
            estimated_cost=totals.estimated_cost,
            unavailable_cost_events=totals.unavailable_cost_events,
            currency=currency,
            total_requests=totals.total_requests,
            total_tokens=totals.total_tokens,
            average_cost_per_request=_average_per_request(totals),
        )


@dataclass(frozen=True, slots=True)
class CostBreakdownEntry:
    """One row of a breakdown, with the display name resolved where possible."""

    id: str | None
    name: str | None
    actual_cost: float
    estimated_cost: float
    currency: str
    total_requests: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class CostTrendEntry:
    """One time bucket of a trend."""

    bucket_start: str
    actual_cost: float
    estimated_cost: float
    currency: str
    total_requests: int
    total_tokens: int


def _average_per_request(totals: CostTotals) -> float | None:
    """Mean cost per request, over the spend that is actually known.

    ``None`` rather than 0.0 when there were no requests: a period with no
    traffic has no average, and rendering one as zero invites it to be read as
    "requests were free".
    """
    if totals.total_requests <= 0:
        return None
    return (totals.actual_cost + totals.estimated_cost) / totals.total_requests


class CostAggregationService:
    """Reporting queries over recorded cost events."""

    def __init__(
        self, repository: CostAggregationRepository, *, base_currency: str
    ) -> None:
        self._repository = repository
        self._base_currency = base_currency

    async def summary(
        self,
        scope: AuthorizedScope,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> CostSummaryResult:
        totals = await self._repository.summary(scope, from_ts=from_ts, to_ts=to_ts)
        return CostSummaryResult.from_totals(totals, currency=self._base_currency)

    async def breakdown(
        self,
        scope: AuthorizedScope,
        dimension: BreakdownDimension,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        names: dict[str, str] | None = None,
    ) -> list[CostBreakdownEntry]:
        """Spend grouped by model, agent or plant.

        ``names`` maps ids to display names when the caller has resolved them.
        An unresolved id yields ``name=None`` rather than a placeholder — the
        row still carries real spend and must not be dropped or relabelled.
        """
        rows = await self._repository.breakdown(
            scope, dimension, from_ts=from_ts, to_ts=to_ts
        )
        lookup = names or {}
        return [
            CostBreakdownEntry(
                id=row.id,
                name=lookup.get(row.id) if row.id else None,
                actual_cost=row.totals.actual_cost,
                estimated_cost=row.totals.estimated_cost,
                currency=self._base_currency,
                total_requests=row.totals.total_requests,
                total_tokens=row.totals.total_tokens,
            )
            for row in rows
        ]

    async def trend(
        self,
        scope: AuthorizedScope,
        granularity: Granularity,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[CostTrendEntry]:
        rows = await self._repository.trend(
            scope, granularity, from_ts=from_ts, to_ts=to_ts
        )
        return [
            CostTrendEntry(
                bucket_start=row.bucket_key,
                actual_cost=row.totals.actual_cost,
                estimated_cost=row.totals.estimated_cost,
                currency=self._base_currency,
                total_requests=row.totals.total_requests,
                total_tokens=row.totals.total_tokens,
            )
            for row in rows
        ]
