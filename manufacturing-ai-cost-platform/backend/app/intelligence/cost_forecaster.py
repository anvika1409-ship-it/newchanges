"""Cost forecasting algorithms and baseline models.

Implements explainable statistical baseline models for:
- Daily horizon forecasting
- 7-day trajectory and totals
- 30-day trajectory and totals
- Month-end projected spend run-rates

All outputs are predictions and strictly marked with provenance="FORECAST"
(AI_DEVELOPMENT_RULES.md sections 41 and 42).
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class DailyForecastPoint:
    """Individual daily cost forecast data point."""

    forecast_date: date
    predicted_cost: float
    lower_bound: float
    upper_bound: float
    confidence: float
    provenance: str = "FORECAST"


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """Complete multi-horizon forecast result."""

    horizon_days: int
    daily_points: list[DailyForecastPoint]
    total_forecasted_cost: float
    month_end_forecast_cost: float
    average_daily_cost: float
    forecast_model_name: str = "baseline_linear_runrate"
    forecast_model_version: str = "1.0.0"
    provenance: str = "FORECAST"
    history_data_points_count: int = 0
    confidence_level: float = 0.95


class CostForecaster:
    """Explainable statistical cost forecaster."""

    def __init__(
        self,
        *,
        model_name: str = "baseline_linear_runrate",
        model_version: str = "1.0.0",
        default_daily_cost: float = 10.0,
    ) -> None:
        self._model_name = model_name
        self._model_version = model_version
        self._default_daily_cost = default_daily_cost

    def generate_forecast(
        self,
        historical_daily_costs: list[dict[str, Any]] | list[tuple[date, float]],
        *,
        horizon_days: int = 30,
        start_date: date | None = None,
    ) -> ForecastResult:
        """Generate daily and cumulative cost forecasts for the specified horizon.

        Args:
            historical_daily_costs: List of (date, cost) tuples or dicts with "date" & "cost".
            horizon_days: Number of days forward to project (e.g. 1, 7, 30).
            start_date: Start date for the forecast (defaults to tomorrow).
        """
        # 1. Normalize historical data
        parsed_history = self._parse_history(historical_daily_costs)
        parsed_history.sort(key=lambda x: x[0])

        current_date = start_date or (date.today() + timedelta(days=1))
        n_history = len(parsed_history)

        # 2. Handle empty or very short history
        if n_history == 0:
            return self._fallback_forecast(
                horizon_days=horizon_days,
                start_date=current_date,
                base_cost=self._default_daily_cost,
                confidence=0.5,
                history_count=0,
            )

        costs = [c for _, c in parsed_history]
        avg_cost = sum(costs) / n_history

        if n_history < 3:
            # Insufficient history for trend analysis; use constant mean with wider bounds
            return self._fallback_forecast(
                horizon_days=horizon_days,
                start_date=current_date,
                base_cost=avg_cost,
                confidence=0.7,
                history_count=n_history,
            )

        # 3. Linear Regression & Residual Variance for baseline trend
        # x: day indices (0, 1, ..., n-1), y: cost values
        x_vals = list(range(n_history))
        y_vals = costs
        mean_x = sum(x_vals) / n_history
        mean_y = avg_cost

        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_vals, y_vals, strict=False))
        denominator = sum((x - mean_x) ** 2 for x in x_vals)

        slope = (numerator / denominator) if denominator != 0 else 0.0
        # Prevent extreme negative slopes from projecting negative costs
        intercept = mean_y - slope * mean_x

        # Calculate standard error of residuals
        residuals = [y - (intercept + slope * x) for x, y in zip(x_vals, y_vals, strict=False)]
        variance = sum(r**2 for r in residuals) / max(1, n_history - 2)
        std_error = math.sqrt(variance)

        # 4. Generate daily forecast points
        daily_points: list[DailyForecastPoint] = []
        total_forecast_cost = 0.0

        for day_offset in range(horizon_days):
            target_date = current_date + timedelta(days=day_offset)
            future_x = n_history + day_offset

            raw_predicted = intercept + slope * future_x
            predicted = max(0.0, raw_predicted)

            # Confidence interval expands slightly with forecast horizon distance
            horizon_spread = 1.0 + (day_offset / 30.0) * 0.5
            margin = 1.96 * std_error * horizon_spread

            lower_b = max(0.0, predicted - margin)
            upper_b = predicted + margin

            point = DailyForecastPoint(
                forecast_date=target_date,
                predicted_cost=round(predicted, 2),
                lower_bound=round(lower_b, 2),
                upper_bound=round(upper_b, 2),
                confidence=0.95,
                provenance="FORECAST",
            )
            daily_points.append(point)
            total_forecast_cost += predicted

        # 5. Month-End Forecast Run-Rate
        month_end_cost = self._compute_month_end_projection(
            current_date=date.today(),
            parsed_history=parsed_history,
            daily_points=daily_points,
            avg_daily_cost=avg_cost,
        )

        return ForecastResult(
            horizon_days=horizon_days,
            daily_points=daily_points,
            total_forecasted_cost=round(total_forecast_cost, 2),
            month_end_forecast_cost=round(month_end_cost, 2),
            average_daily_cost=round(avg_cost, 2),
            forecast_model_name=self._model_name,
            forecast_model_version=self._model_version,
            provenance="FORECAST",
            history_data_points_count=n_history,
            confidence_level=0.95,
        )

    # ── Helpers ───────────────────────────────────────────────────

    def _fallback_forecast(
        self,
        *,
        horizon_days: int,
        start_date: date,
        base_cost: float,
        confidence: float,
        history_count: int,
    ) -> ForecastResult:
        daily_points: list[DailyForecastPoint] = []
        total_cost = 0.0

        for day_offset in range(horizon_days):
            target_date = start_date + timedelta(days=day_offset)
            predicted = max(0.0, base_cost)
            lower_b = max(0.0, predicted * 0.7)
            upper_b = predicted * 1.3

            point = DailyForecastPoint(
                forecast_date=target_date,
                predicted_cost=round(predicted, 2),
                lower_bound=round(lower_b, 2),
                upper_bound=round(upper_b, 2),
                confidence=confidence,
                provenance="FORECAST",
            )
            daily_points.append(point)
            total_cost += predicted

        today = date.today()
        _, num_days_in_month = calendar.monthrange(today.year, today.month)
        month_end_cost = round(base_cost * num_days_in_month, 2)

        return ForecastResult(
            horizon_days=horizon_days,
            daily_points=daily_points,
            total_forecasted_cost=round(total_cost, 2),
            month_end_forecast_cost=month_end_cost,
            average_daily_cost=round(base_cost, 2),
            forecast_model_name=self._model_name,
            forecast_model_version=self._model_version,
            provenance="FORECAST",
            history_data_points_count=history_count,
            confidence_level=confidence,
        )

    @staticmethod
    def _compute_month_end_projection(
        *,
        current_date: date,
        parsed_history: list[tuple[date, float]],
        daily_points: list[DailyForecastPoint],
        avg_daily_cost: float,
    ) -> float:
        """Project total spend through end of current calendar month."""
        year = current_date.year
        month = current_date.month
        _, num_days = calendar.monthrange(year, month)

        # Actual spend in current month so far
        month_actual = sum(
            cost for d, cost in parsed_history if d.year == year and d.month == month
        )

        # Remaining days in current month
        remaining_days = num_days - current_date.day
        if remaining_days <= 0:
            return month_actual

        # Use forward projections for remaining days if available, else average rate
        future_in_month = sum(
            p.predicted_cost
            for p in daily_points
            if p.forecast_date.year == year and p.forecast_date.month == month
        )

        if future_in_month > 0:
            return month_actual + future_in_month
        return month_actual + (avg_daily_cost * remaining_days)

    @staticmethod
    def _parse_history(
        raw_history: list[dict[str, Any]] | list[tuple[date, float]]
    ) -> list[tuple[date, float]]:
        result = []
        for item in raw_history:
            if isinstance(item, tuple) and len(item) == 2:
                d, c = item
                if isinstance(d, str):
                    d = date.fromisoformat(d)
                result.append((d, float(c)))
            elif isinstance(item, dict):
                d_val = item.get("date") or item.get("forecast_date") or item.get("timestamp")
                c_val = item.get("cost") or item.get("amount") or item.get("predicted_cost", 0.0)
                if isinstance(d_val, str):
                    d_val = date.fromisoformat(d_val[:10])
                elif isinstance(d_val, datetime):
                    d_val = d_val.date()
                if d_val is not None:
                    result.append((d_val, float(c_val)))
        return result
