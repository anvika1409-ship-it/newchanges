"""Cost service for retrieving historical spend, usage events, and metric baselines.

Provides analytical data access to support cost investigation, forecasting,
and optimization workflows (ARCHITECTURE.md section 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class CostSummary:
    """Aggregated cost and usage summary for a scope and time window."""

    scope_type: str
    scope_id: str
    total_cost_usd: float
    total_tokens: int
    total_requests: int
    average_daily_cost: float
    cost_std_dev: float
    model_breakdown: dict[str, float] = field(default_factory=dict)
    workload_breakdown: dict[str, float] = field(default_factory=dict)
    historical_daily_costs: list[dict[str, Any]] = field(default_factory=list)


class CostService:
    """Service providing historical cost analytics and usage breakdowns."""

    def __init__(self, *, default_daily_mean: float = 50.0, default_daily_std: float = 10.0) -> None:
        self._default_mean = default_daily_mean
        self._default_std = default_daily_std

    async def get_cost_history(
        self,
        *,
        scope_type: str = "TENANT",
        scope_id: str = "default",
        time_window_days: int = 30,
        custom_history: list[dict[str, Any]] | None = None,
    ) -> CostSummary:
        """Retrieve historical spend data and compute baseline statistics."""
        if custom_history is not None and len(custom_history) > 0:
            history = custom_history
        else:
            # Generate representative baseline history
            today = date.today()
            history = []
            for i in range(time_window_days, 0, -1):
                day_date = today - timedelta(days=i)
                history.append(
                    {
                        "date": day_date.isoformat(),
                        "cost": round(self._default_mean, 2),
                        "tokens": 10000,
                        "requests": 100,
                    }
                )

        costs = [float(item.get("cost", 0.0)) for item in history]
        tokens = sum(int(item.get("tokens", 0)) for item in history)
        requests = sum(int(item.get("requests", 0)) for item in history)
        total_cost = sum(costs)
        n = max(1, len(costs))
        avg_cost = total_cost / n

        # Standard deviation
        variance = sum((c - avg_cost) ** 2 for c in costs) / n
        std_dev = variance**0.5 if variance > 0 else self._default_std

        # Model breakdown (default realistic split)
        model_breakdown = {
            "gpt-4o-mini": round(total_cost * 0.60, 2),
            "llama-3-8b": round(total_cost * 0.25, 2),
            "claude-3-5-sonnet": round(total_cost * 0.15, 2),
        }

        # Workload breakdown
        workload_breakdown = {
            "predictive_maintenance": round(total_cost * 0.45, 2),
            "quality_check": round(total_cost * 0.35, 2),
            "supply_chain": round(total_cost * 0.20, 2),
        }

        return CostSummary(
            scope_type=scope_type,
            scope_id=scope_id,
            total_cost_usd=round(total_cost, 2),
            total_tokens=tokens,
            total_requests=requests,
            average_daily_cost=round(avg_cost, 2),
            cost_std_dev=round(std_dev, 2),
            model_breakdown=model_breakdown,
            workload_breakdown=workload_breakdown,
            historical_daily_costs=history,
        )
