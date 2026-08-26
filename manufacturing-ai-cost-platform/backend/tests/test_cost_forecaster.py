"""Unit tests for CostForecaster.

Tests cover:
- Daily, 7-day, 30-day, and month-end forecast generation
- Empty history handling
- Insufficient (<3 points) history handling
- Normal trend forecasting
- Provenance labeling (FORECAST)
- Confidence intervals
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.intelligence.cost_forecaster import CostForecaster, DailyForecastPoint, ForecastResult


@pytest.fixture
def forecaster() -> CostForecaster:
    return CostForecaster()


def _generate_linear_history(
    n_days: int = 30, base: float = 100.0, slope: float = 2.0
) -> list[tuple[date, float]]:
    start = date.today() - timedelta(days=n_days)
    return [(start + timedelta(days=i), base + slope * i) for i in range(n_days)]


class TestCostForecaster:
    """Test suite for the explainable baseline cost forecaster."""

    def test_normal_trend_forecast(self, forecaster: CostForecaster) -> None:
        """30-day steady history produces realistic forward projections."""
        history = _generate_linear_history(n_days=30, base=100.0, slope=2.0)
        result = forecaster.generate_forecast(history, horizon_days=30)

        assert isinstance(result, ForecastResult)
        assert result.horizon_days == 30
        assert len(result.daily_points) == 30
        assert result.provenance == "FORECAST"
        assert result.confidence_level == 0.95

        # Verify daily points
        first_point = result.daily_points[0]
        assert isinstance(first_point, DailyForecastPoint)
        assert first_point.provenance == "FORECAST"
        assert first_point.lower_bound <= first_point.predicted_cost <= first_point.upper_bound
        assert first_point.predicted_cost > 0

        # With positive slope, forecast cost should increase over time
        last_point = result.daily_points[-1]
        assert last_point.predicted_cost > first_point.predicted_cost
        assert result.total_forecasted_cost > 0
        assert result.month_end_forecast_cost > 0

    def test_7_day_forecast(self, forecaster: CostForecaster) -> None:
        """7-day horizon projection."""
        history = _generate_linear_history(n_days=14, base=50.0, slope=1.0)
        result = forecaster.generate_forecast(history, horizon_days=7)

        assert result.horizon_days == 7
        assert len(result.daily_points) == 7
        assert result.provenance == "FORECAST"

    def test_empty_history_fallback(self, forecaster: CostForecaster) -> None:
        """Empty history returns fallback prediction without error."""
        result = forecaster.generate_forecast([], horizon_days=30)

        assert result.horizon_days == 30
        assert len(result.daily_points) == 30
        assert result.provenance == "FORECAST"
        assert result.confidence_level == 0.5
        assert result.history_data_points_count == 0
        assert result.total_forecasted_cost > 0

    def test_insufficient_history(self, forecaster: CostForecaster) -> None:
        """Single or 2 data points use average baseline with lower confidence."""
        history = [(date.today() - timedelta(days=1), 45.0)]
        result = forecaster.generate_forecast(history, horizon_days=10)

        assert result.horizon_days == 10
        assert len(result.daily_points) == 10
        assert result.provenance == "FORECAST"
        assert result.confidence_level == 0.7
        assert result.history_data_points_count == 1
        assert result.average_daily_cost == 45.0

    def test_dict_history_input_format(self, forecaster: CostForecaster) -> None:
        """Accepts dict-based history input format."""
        history_dicts = [
            {"date": (date.today() - timedelta(days=i)).isoformat(), "cost": 50.0 + i}
            for i in range(10, 0, -1)
        ]
        result = forecaster.generate_forecast(history_dicts, horizon_days=14)

        assert result.horizon_days == 14
        assert len(result.daily_points) == 14
        assert result.provenance == "FORECAST"

    def test_month_end_projection_is_positive(self, forecaster: CostForecaster) -> None:
        """Month-end projection calculates positive run-rate."""
        history = _generate_linear_history(n_days=20, base=80.0, slope=0.5)
        result = forecaster.generate_forecast(history, horizon_days=30)

        assert result.month_end_forecast_cost > 0
        assert result.month_end_forecast_cost >= result.average_daily_cost
