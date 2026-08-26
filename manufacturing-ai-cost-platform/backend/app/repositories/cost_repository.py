"""Cost aggregation repository.

``CostEventRepository`` in ``telemetry_repository`` writes single cost events.
This repository reads them back in aggregate: summaries, breakdowns by model,
agent or plant, and time-bucketed trends, all constrained to what the caller may
see.

Three rules shape every query here.

**Aggregation happens in SQL, not in Python.** Pulling telemetry rows into the
process to sum them would not survive a real workload and would make pagination
totals lie.

**Actual and estimated are summed separately.** They are never added into one
figure (AI_DEVELOPMENT_RULES.md sections 41 and 42), and UNAVAILABLE events are
counted rather than treated as zero — a request whose cost is unknown is not a
free request.

**Dialect-specific SQL stays here.** Date bucketing has no portable spelling, so
it is confined to ``_bucket_expression`` in this infrastructure adapter and
never reaches a service (ARCHITECTURE.md section 12, AI_DEVELOPMENT_RULES.md
section 16). SQLite and PostgreSQL are implemented; any other dialect raises
rather than silently producing wrong buckets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, case, func, select
from sqlalchemy.orm import InstrumentedAttribute

from app.db.models.telemetry import CostEvent, UsageEvent
from app.repositories.base import AsyncRepository
from app.repositories.scope_filter import authorized_scope_filter
from app.security.scope import AuthorizedScope

#: Cost events whose provenance is neither ACTUAL nor ESTIMATED contribute no
#: money to any total; they are counted instead.
ACTUAL = "ACTUAL"
ESTIMATED = "ESTIMATED"


class Granularity(StrEnum):
    """Trend bucket sizes (API_CONTRACT.yaml ``CostTrend.granularity``)."""

    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class BreakdownDimension(StrEnum):
    """Grouping dimensions (API_CONTRACT.yaml ``CostBreakdown.dimension``)."""

    MODEL = "model"
    AGENT = "agent"
    PLANT = "plant"


_DIMENSION_COLUMN: dict[BreakdownDimension, str] = {
    BreakdownDimension.MODEL: "model_id",
    BreakdownDimension.AGENT: "agent_id",
    BreakdownDimension.PLANT: "plant_id",
}

#: SQLite strftime patterns per bucket. ``%W`` is the ISO-ish week number, which
#: keeps weekly buckets stable within a year.
_SQLITE_BUCKET_FORMAT: dict[Granularity, str] = {
    Granularity.HOUR: "%Y-%m-%dT%H:00:00",
    Granularity.DAY: "%Y-%m-%dT00:00:00",
    Granularity.WEEK: "%Y-W%W",
    Granularity.MONTH: "%Y-%m-01T00:00:00",
}


@dataclass(frozen=True, slots=True)
class CostTotals:
    """Aggregate figures for one group of cost events.

    ``actual_cost`` and ``estimated_cost`` are separate because they mean
    different things and must never be blended. ``unavailable_cost_events``
    counts executions whose cost could not be computed at all.
    """

    actual_cost: float
    estimated_cost: float
    unavailable_cost_events: int
    total_requests: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class BreakdownRow:
    """One grouped row, keyed by the dimension's id."""

    id: str | None
    totals: CostTotals


@dataclass(frozen=True, slots=True)
class TrendRow:
    """One time bucket. ``bucket_key`` is the dialect's bucket label."""

    bucket_key: str
    totals: CostTotals


@dataclass(frozen=True, slots=True)
class ScopeSpend:
    """Spend recorded against one budget scope in a period."""

    actual_cost: float
    estimated_cost: float
    unavailable_cost_events: int


class CostAggregationRepository(AsyncRepository[CostEvent]):
    """Read-side aggregation over ``cost_events`` joined to ``usage_events``.

    The join is required: ``cost_events`` carries the money, ``usage_events``
    carries the tenant, plant, department and timestamp that every query filters
    on (DATABASE_SCHEMA.md sections 14 and 15).
    """

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _sum_where(
        column: InstrumentedAttribute[float | None], provenance: str
    ) -> ColumnElement[float]:
        """Sum a cost column only for rows with the given provenance.

        ``coalesce`` turns SQL's NULL-for-empty-sum into 0.0 so callers get a
        number. That is safe here because it is a sum of *no rows*, not a
        missing price being read as free.
        """
        return func.coalesce(
            func.sum(case((CostEvent.provenance == provenance, column), else_=None)),
            0.0,
        )

    @classmethod
    def _totals_columns(cls) -> list[Any]:
        return [
            cls._sum_where(CostEvent.actual_cost, ACTUAL).label("actual_cost"),
            cls._sum_where(CostEvent.estimated_cost, ESTIMATED).label("estimated_cost"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            CostEvent.provenance.notin_((ACTUAL, ESTIMATED)),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("unavailable_cost_events"),
            func.count(func.distinct(UsageEvent.id)).label("total_requests"),
            func.coalesce(func.sum(UsageEvent.total_tokens), 0).label("total_tokens"),
        ]

    @staticmethod
    def _row_to_totals(row: object) -> CostTotals:
        return CostTotals(
            actual_cost=float(row.actual_cost or 0.0),  # type: ignore[attr-defined]
            estimated_cost=float(row.estimated_cost or 0.0),  # type: ignore[attr-defined]
            unavailable_cost_events=int(row.unavailable_cost_events or 0),  # type: ignore[attr-defined]
            total_requests=int(row.total_requests or 0),  # type: ignore[attr-defined]
            total_tokens=int(row.total_tokens or 0),  # type: ignore[attr-defined]
        )

    def _base_select(
        self,
        scope: AuthorizedScope,
        *,
        from_ts: datetime | None,
        to_ts: datetime | None,
        columns: list[ColumnElement[object]],
    ) -> Select[tuple[object, ...]]:
        """Join, scope and date-bound a query.

        The scope predicate is applied here rather than by the caller so no
        aggregation path can accidentally omit it.
        """
        stmt = select(*columns).select_from(
            CostEvent.__table__.join(
                UsageEvent.__table__, CostEvent.usage_event_id == UsageEvent.id
            )
        )
        stmt = stmt.where(
            authorized_scope_filter(
                scope,
                tenant_column=UsageEvent.tenant_id,
                plant_column=UsageEvent.plant_id,
                department_column=UsageEvent.department_id,
            )
        )
        if from_ts is not None:
            stmt = stmt.where(UsageEvent.timestamp >= from_ts)
        if to_ts is not None:
            # Inclusive upper bound, matching the contract's `to` parameter
            # reading as "up to and including this instant".
            stmt = stmt.where(UsageEvent.timestamp <= to_ts)
        return stmt

    def _bucket_expression(self, granularity: Granularity) -> ColumnElement[str]:
        """Dialect-specific bucket key. The only such expression in the codebase.

        Raises:
            NotImplementedError: on a dialect with no implemented bucketing.
                Failing loudly beats emitting buckets that are quietly wrong on
                a database nobody has tested.
        """
        dialect = self.session.bind.dialect.name if self.session.bind else "sqlite"

        if dialect == "sqlite":
            return func.strftime(_SQLITE_BUCKET_FORMAT[granularity], UsageEvent.timestamp)
        if dialect == "postgresql":
            return func.to_char(
                func.date_trunc(granularity.value, UsageEvent.timestamp),
                "YYYY-MM-DD\"T\"HH24:MI:SS",
            )
        raise NotImplementedError(
            f"Cost trend bucketing is not implemented for the {dialect!r} dialect. "
            "Add its date-truncation expression to "
            "CostAggregationRepository._bucket_expression."
        )

    # ------------------------------------------------------------- summary
    async def summary(
        self,
        scope: AuthorizedScope,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> CostTotals:
        """Aggregate spend across everything the caller may see."""
        stmt = self._base_select(
            scope, from_ts=from_ts, to_ts=to_ts, columns=self._totals_columns()
        )
        row = (await self.session.execute(stmt)).one()
        return self._row_to_totals(row)

    # ----------------------------------------------------------- breakdown
    async def breakdown(
        self,
        scope: AuthorizedScope,
        dimension: BreakdownDimension,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[BreakdownRow]:
        """Aggregate grouped by model, agent or plant.

        Ordered by the grouping id so repeated calls return rows in the same
        order — the acceptance criterion covers ordering, not just values.
        """
        group_column: InstrumentedAttribute[str | None] = getattr(
            UsageEvent, _DIMENSION_COLUMN[dimension]
        )
        columns: list[Any] = [
            group_column.label("group_id"),
            *self._totals_columns(),
        ]
        stmt = self._base_select(
            scope, from_ts=from_ts, to_ts=to_ts, columns=columns
        ).group_by(group_column).order_by(group_column)

        rows = (await self.session.execute(stmt)).all()
        return [
            BreakdownRow(id=row.group_id, totals=self._row_to_totals(row))
            for row in rows
        ]

    # --------------------------------------------------------------- trend
    async def trend(
        self,
        scope: AuthorizedScope,
        granularity: Granularity,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[TrendRow]:
        """Aggregate into time buckets, ordered oldest first."""
        bucket = self._bucket_expression(granularity).label("bucket_key")
        columns: list[Any] = [bucket, *self._totals_columns()]
        stmt = (
            self._base_select(scope, from_ts=from_ts, to_ts=to_ts, columns=columns)
            .group_by(bucket)
            .order_by(bucket)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            TrendRow(bucket_key=str(row.bucket_key), totals=self._row_to_totals(row))
            for row in rows
        ]

    # -------------------------------------------------------- budget spend
    async def spend_for_scope(
        self,
        tenant_id: str,
        scope_type: str,
        scope_id: str,
        *,
        from_ts: datetime,
        to_ts: datetime,
    ) -> ScopeSpend:
        """Spend recorded against one budget scope within a period.

        Scope columns are matched directly on ``usage_events``. ENTERPRISE has
        no column of its own — DATABASE_SCHEMA.md section 12 notes it has no
        parent entity to derive tenancy from — so it matches the whole tenant.

        This query is tenant-filtered by ``tenant_id`` rather than by an
        ``AuthorizedScope``: it answers "how full is this budget", which must
        reflect *all* spend against the budget, not only the part the requesting
        caller happens to be allowed to see. Authorization to read the budget
        itself is enforced before this is called.
        """
        conditions: list[ColumnElement[bool]] = [UsageEvent.tenant_id == tenant_id]

        column_by_scope: dict[str, InstrumentedAttribute[str | None]] = {
            "TENANT": UsageEvent.tenant_id,
            "PLANT": UsageEvent.plant_id,
            "DEPARTMENT": UsageEvent.department_id,
            "WORKLOAD": UsageEvent.workload_id,
            "AGENT": UsageEvent.agent_id,
            "MODEL": UsageEvent.model_id,
        }
        scoped_column = column_by_scope.get(scope_type)
        if scoped_column is not None:
            conditions.append(scoped_column == scope_id)
        # ENTERPRISE falls through with the tenant filter only.

        stmt = (
            select(
                self._sum_where(CostEvent.actual_cost, ACTUAL).label("actual_cost"),
                self._sum_where(CostEvent.estimated_cost, ESTIMATED).label(
                    "estimated_cost"
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (CostEvent.provenance.notin_((ACTUAL, ESTIMATED)), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("unavailable_cost_events"),
            )
            .select_from(
                CostEvent.__table__.join(
                    UsageEvent.__table__, CostEvent.usage_event_id == UsageEvent.id
                )
            )
            .where(
                and_(
                    *conditions,
                    UsageEvent.timestamp >= from_ts,
                    UsageEvent.timestamp <= to_ts,
                )
            )
        )

        row = (await self.session.execute(stmt)).one()
        return ScopeSpend(
            actual_cost=float(row.actual_cost or 0.0),
            estimated_cost=float(row.estimated_cost or 0.0),
            unavailable_cost_events=int(row.unavailable_cost_events or 0),
        )
