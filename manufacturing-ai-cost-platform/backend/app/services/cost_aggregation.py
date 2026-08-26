"""Cost aggregation service.

Aggregates AI execution telemetry across scopes, models, agents, plants, and time buckets.
Enforces strict actual vs. estimated cost separation (AI_DEVELOPMENT_RULES.md sections 41 & 42).
"""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.api.v1.schemas.costs import (
    CostBreakdown,
    CostBreakdownItem,
    CostSummary,
    CostTrend,
    CostTrendPoint,
)
from app.core.logging import get_logger
from app.db.models.telemetry import UsageEvent
from app.repositories.budget_repository import BudgetRepository
from app.repositories.telemetry_repository import CostEventRepository, UsageEventRepository

logger = get_logger(__name__)


class CostAggregationService:
    """Service for querying and computing multi-dimensional AI cost metrics."""

    def __init__(
        self,
        usage_repo: UsageEventRepository,
        cost_repo: CostEventRepository | None = None,
        budget_repo: BudgetRepository | None = None,
    ) -> None:
        self._usage_repo = usage_repo
        self._cost_repo = cost_repo
        self._budget_repo = budget_repo

    async def get_cost_summary(
        self,
        tenant_id: str,
        *,
        plant_id: str | None = None,
        department_id: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> CostSummary:
        """Compute top-level summary of actual spend, estimated spend, token volume, and budget consumption."""
        events = await self._usage_repo.query_telemetry(
            tenant_id,
            plant_id=plant_id,
            department_id=department_id,
            from_ts=from_ts,
            to_ts=to_ts,
        )

        total_requests = len(events)
        actual_cost = 0.0
        estimated_cost = 0.0
        unavailable_count = 0
        total_tokens = 0

        for event in events:
            total_tokens += event.total_tokens or (
                (event.input_tokens or 0) + (event.output_tokens or 0)
            )

            ce = event.cost_event
            if ce is None:
                unavailable_count += 1
                continue

            prov = (ce.provenance or "UNAVAILABLE").upper()
            if prov == "ACTUAL":
                actual_cost += ce.actual_cost if ce.actual_cost is not None else (ce.estimated_cost or 0.0)
            elif prov == "ESTIMATED":
                estimated_cost += ce.estimated_cost if ce.estimated_cost is not None else 0.0
            else:
                unavailable_count += 1

        total_spend = actual_cost + estimated_cost
        avg_cost = (total_spend / total_requests) if total_requests > 0 else 0.0

        # Calculate budget consumption if budget repository is available
        budget_consumed_percent = 0.0
        if self._budget_repo is not None:
            scope_type = "DEPARTMENT" if department_id else ("PLANT" if plant_id else "TENANT")
            scope_id = department_id or (plant_id or tenant_id)
            budgets = await self._budget_repo.list_by_tenant(tenant_id, scope_type=scope_type, scope_id=scope_id)
            if budgets:
                active_budget = next((b for b in budgets if b.status == "ACTIVE"), budgets[0])
                if active_budget.amount > 0:
                    budget_consumed_percent = round((total_spend / active_budget.amount) * 100.0, 2)

        # Forecast month-end cost based on current daily run-rate
        forecast_month_end = 0.0
        now = datetime.now(UTC)
        days_in_month = monthrange(now.year, now.month)[1]
        current_day = max(1, now.day)
        if total_spend > 0:
            daily_run_rate = total_spend / current_day
            forecast_month_end = round(daily_run_rate * days_in_month, 2)

        return CostSummary(
            actual_cost=round(actual_cost, 4),
            estimated_cost=round(estimated_cost, 4),
            unavailable_cost_events=unavailable_count,
            currency="USD",
            total_requests=total_requests,
            total_tokens=total_tokens,
            average_cost_per_request=round(avg_cost, 6),
            budget_consumed_percent=budget_consumed_percent,
            forecast_month_end_cost=forecast_month_end,
        )

    async def get_cost_by_model(
        self,
        tenant_id: str,
        *,
        plant_id: str | None = None,
        department_id: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> CostBreakdown:
        """Compute cost and token breakdown grouped by AI model."""
        events = await self._usage_repo.query_telemetry(
            tenant_id,
            plant_id=plant_id,
            department_id=department_id,
            from_ts=from_ts,
            to_ts=to_ts,
        )

        groups: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "actual_cost": 0.0,
                "estimated_cost": 0.0,
                "total_requests": 0,
                "total_tokens": 0,
            }
        )

        for event in events:
            model_key = event.model_id or "unspecified-model"
            entry = groups[model_key]
            entry["total_requests"] += 1
            entry["total_tokens"] += event.total_tokens or (
                (event.input_tokens or 0) + (event.output_tokens or 0)
            )

            ce = event.cost_event
            if ce is not None:
                prov = (ce.provenance or "UNAVAILABLE").upper()
                if prov == "ACTUAL":
                    entry["actual_cost"] += ce.actual_cost if ce.actual_cost is not None else (ce.estimated_cost or 0.0)
                elif prov == "ESTIMATED":
                    entry["estimated_cost"] += ce.estimated_cost if ce.estimated_cost is not None else 0.0

        items = [
            CostBreakdownItem(
                id=k,
                name=k,
                actual_cost=round(v["actual_cost"], 4),
                estimated_cost=round(v["estimated_cost"], 4),
                currency="USD",
                total_requests=v["total_requests"],
                total_tokens=v["total_tokens"],
            )
            for k, v in sorted(groups.items(), key=lambda x: -(x[1]["actual_cost"] + x[1]["estimated_cost"]))
        ]

        return CostBreakdown(dimension="model", items=items)

    async def get_cost_by_agent(
        self,
        tenant_id: str,
        *,
        plant_id: str | None = None,
        department_id: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> CostBreakdown:
        """Compute cost and token breakdown grouped by AI agent."""
        events = await self._usage_repo.query_telemetry(
            tenant_id,
            plant_id=plant_id,
            department_id=department_id,
            from_ts=from_ts,
            to_ts=to_ts,
        )

        groups: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "actual_cost": 0.0,
                "estimated_cost": 0.0,
                "total_requests": 0,
                "total_tokens": 0,
            }
        )

        for event in events:
            agent_key = event.agent_id or "unspecified-agent"
            entry = groups[agent_key]
            entry["total_requests"] += 1
            entry["total_tokens"] += event.total_tokens or (
                (event.input_tokens or 0) + (event.output_tokens or 0)
            )

            ce = event.cost_event
            if ce is not None:
                prov = (ce.provenance or "UNAVAILABLE").upper()
                if prov == "ACTUAL":
                    entry["actual_cost"] += ce.actual_cost if ce.actual_cost is not None else (ce.estimated_cost or 0.0)
                elif prov == "ESTIMATED":
                    entry["estimated_cost"] += ce.estimated_cost if ce.estimated_cost is not None else 0.0

        items = [
            CostBreakdownItem(
                id=k,
                name=k,
                actual_cost=round(v["actual_cost"], 4),
                estimated_cost=round(v["estimated_cost"], 4),
                currency="USD",
                total_requests=v["total_requests"],
                total_tokens=v["total_tokens"],
            )
            for k, v in sorted(groups.items(), key=lambda x: -(x[1]["actual_cost"] + x[1]["estimated_cost"]))
        ]

        return CostBreakdown(dimension="agent", items=items)

    async def get_cost_by_plant(
        self,
        tenant_id: str,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> CostBreakdown:
        """Compute cost and token breakdown grouped by plant."""
        events = await self._usage_repo.query_telemetry(
            tenant_id,
            from_ts=from_ts,
            to_ts=to_ts,
        )

        groups: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "actual_cost": 0.0,
                "estimated_cost": 0.0,
                "total_requests": 0,
                "total_tokens": 0,
            }
        )

        for event in events:
            plant_key = event.plant_id or "unspecified-plant"
            entry = groups[plant_key]
            entry["total_requests"] += 1
            entry["total_tokens"] += event.total_tokens or (
                (event.input_tokens or 0) + (event.output_tokens or 0)
            )

            ce = event.cost_event
            if ce is not None:
                prov = (ce.provenance or "UNAVAILABLE").upper()
                if prov == "ACTUAL":
                    entry["actual_cost"] += ce.actual_cost if ce.actual_cost is not None else (ce.estimated_cost or 0.0)
                elif prov == "ESTIMATED":
                    entry["estimated_cost"] += ce.estimated_cost if ce.estimated_cost is not None else 0.0

        items = [
            CostBreakdownItem(
                id=k,
                name=k,
                actual_cost=round(v["actual_cost"], 4),
                estimated_cost=round(v["estimated_cost"], 4),
                currency="USD",
                total_requests=v["total_requests"],
                total_tokens=v["total_tokens"],
            )
            for k, v in sorted(groups.items(), key=lambda x: -(x[1]["actual_cost"] + x[1]["estimated_cost"]))
        ]

        return CostBreakdown(dimension="plant", items=items)

    async def get_cost_trend(
        self,
        tenant_id: str,
        *,
        plant_id: str | None = None,
        department_id: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        granularity: Literal["hour", "day", "week", "month"] = "day",
    ) -> CostTrend:
        """Compute time-series cost trend aggregated into time buckets."""
        events = await self._usage_repo.query_telemetry(
            tenant_id,
            plant_id=plant_id,
            department_id=department_id,
            from_ts=from_ts,
            to_ts=to_ts,
        )

        def _get_bucket_key(ts: datetime, gran: str) -> datetime:
            # Ensure UTC timezone
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if gran == "hour":
                return datetime(ts.year, ts.month, ts.day, ts.hour, 0, 0, tzinfo=UTC)
            if gran == "day":
                return datetime(ts.year, ts.month, ts.day, 0, 0, 0, tzinfo=UTC)
            if gran == "week":
                start_of_week = ts - timedelta(days=ts.weekday())
                return datetime(start_of_week.year, start_of_week.month, start_of_week.day, 0, 0, 0, tzinfo=UTC)
            if gran == "month":
                return datetime(ts.year, ts.month, 1, 0, 0, 0, tzinfo=UTC)
            return datetime(ts.year, ts.month, ts.day, 0, 0, 0, tzinfo=UTC)

        buckets: dict[datetime, dict[str, Any]] = defaultdict(
            lambda: {
                "actual_cost": 0.0,
                "estimated_cost": 0.0,
                "total_requests": 0,
                "total_tokens": 0,
            }
        )

        for event in events:
            bucket_time = _get_bucket_key(event.timestamp, granularity)
            b = buckets[bucket_time]
            b["total_requests"] += 1
            b["total_tokens"] += event.total_tokens or (
                (event.input_tokens or 0) + (event.output_tokens or 0)
            )

            ce = event.cost_event
            if ce is not None:
                prov = (ce.provenance or "UNAVAILABLE").upper()
                if prov == "ACTUAL":
                    b["actual_cost"] += ce.actual_cost if ce.actual_cost is not None else (ce.estimated_cost or 0.0)
                elif prov == "ESTIMATED":
                    b["estimated_cost"] += ce.estimated_cost if ce.estimated_cost is not None else 0.0

        points = [
            CostTrendPoint(
                bucket_start=k,
                actual_cost=round(v["actual_cost"], 4),
                estimated_cost=round(v["estimated_cost"], 4),
                currency="USD",
                total_requests=v["total_requests"],
                total_tokens=v["total_tokens"],
            )
            for k, v in sorted(buckets.items(), key=lambda x: x[0])
        ]

        return CostTrend(granularity=granularity, points=points)
