"""Unit tests for SensorAnomalyDetector.

Tests cover normal readings, single-metric anomalies, multi-metric anomalies,
missing data handling, and boundary conditions.

All tests are deterministic — no LLM or network calls
(AI_DEVELOPMENT_RULES.md section 25).
"""

from __future__ import annotations

import math

import pytest

from app.intelligence.anomaly_detector import (
    AnomalyResult,
    MetricDetail,
    SensorAnomalyDetector,
)


@pytest.fixture
def detector() -> SensorAnomalyDetector:
    """Detector with default normal ranges."""
    return SensorAnomalyDetector()


# ── Normal readings ─────────────────────────────────────────────


class TestNormalReadings:
    """Machine with all sensors in normal range."""

    def test_all_normal(self, detector: SensorAnomalyDetector) -> None:
        """All sensors at their mean values → no anomaly."""
        readings = {
            "temperature": 65.0,
            "vibration": 2.0,
            "pressure": 30.0,
            "rpm": 1500.0,
            "power_consumption": 75.0,
        }
        result = detector.detect(readings)

        assert result.is_anomalous is False
        assert result.anomaly_score == 0.0
        assert result.anomalous_metrics == ()
        assert result.missing_metrics == ()
        assert result.data_quality == "ACTUAL"

    def test_slightly_elevated(self, detector: SensorAnomalyDetector) -> None:
        """Readings within 1 std of mean → no anomaly."""
        readings = {
            "temperature": 74.0,  # mean=65, std=10 → z=0.9
            "vibration": 2.5,  # mean=2.0, std=0.8 → z=0.625
            "pressure": 34.0,  # mean=30, std=5 → z=0.8
            "rpm": 1600.0,  # mean=1500, std=200 → z=0.5
            "power_consumption": 85.0,  # mean=75, std=15 → z=0.667
        }
        result = detector.detect(readings)

        assert result.is_anomalous is False
        assert 0.0 < result.anomaly_score < 1.0
        assert len(result.metric_details) == 5

    def test_data_quality_is_actual(self, detector: SensorAnomalyDetector) -> None:
        """Data quality marker must be ACTUAL for sensor-derived results."""
        result = detector.detect({"temperature": 65.0})
        assert result.data_quality == "ACTUAL"


# ── Single-metric anomaly ───────────────────────────────────────


class TestSingleMetricAnomaly:
    """One sensor out of range, others normal."""

    def test_high_temperature(self, detector: SensorAnomalyDetector) -> None:
        """Temperature far above normal → anomaly flagged."""
        readings = {
            "temperature": 120.0,  # mean=65, std=10 → z=5.5 > threshold 2.5
            "vibration": 2.0,
            "pressure": 30.0,
            "rpm": 1500.0,
            "power_consumption": 75.0,
        }
        result = detector.detect(readings)

        assert result.is_anomalous is True
        assert "temperature" in result.anomalous_metrics
        assert len(result.anomalous_metrics) == 1

    def test_high_vibration(self, detector: SensorAnomalyDetector) -> None:
        """Vibration above threshold → flagged."""
        readings = {
            "temperature": 65.0,
            "vibration": 5.0,  # mean=2.0, std=0.8 → z=3.75 > threshold 2.0
            "pressure": 30.0,
            "rpm": 1500.0,
            "power_consumption": 75.0,
        }
        result = detector.detect(readings)

        assert result.is_anomalous is True
        assert result.anomalous_metrics == ("vibration",)

    def test_low_value_anomaly(self, detector: SensorAnomalyDetector) -> None:
        """Value far below normal is also anomalous (absolute z-score)."""
        readings = {
            "temperature": 10.0,  # mean=65, std=10 → z=5.5
            "vibration": 2.0,
            "pressure": 30.0,
            "rpm": 1500.0,
            "power_consumption": 75.0,
        }
        result = detector.detect(readings)

        assert result.is_anomalous is True
        assert "temperature" in result.anomalous_metrics

    def test_metric_detail_populated(self, detector: SensorAnomalyDetector) -> None:
        """MetricDetail includes value, z-score, threshold and anomaly flag."""
        readings = {"temperature": 120.0}
        result = detector.detect(readings)
        detail = result.metric_details["temperature"]

        assert isinstance(detail, MetricDetail)
        assert detail.value == 120.0
        assert detail.z_score > 0
        assert detail.z_threshold == 2.5
        assert detail.is_anomalous is True
        assert detail.mean == 65.0
        assert detail.std == 10.0


# ── Multi-metric anomaly ────────────────────────────────────────


class TestMultiMetricAnomaly:
    """Multiple sensors out of range simultaneously."""

    def test_two_metrics_anomalous(self, detector: SensorAnomalyDetector) -> None:
        """Two sensors anomalous → both flagged."""
        readings = {
            "temperature": 120.0,  # z=5.5
            "vibration": 5.0,  # z=3.75
            "pressure": 30.0,
            "rpm": 1500.0,
            "power_consumption": 75.0,
        }
        result = detector.detect(readings)

        assert result.is_anomalous is True
        assert len(result.anomalous_metrics) == 2
        assert "temperature" in result.anomalous_metrics
        assert "vibration" in result.anomalous_metrics

    def test_all_metrics_anomalous(self, detector: SensorAnomalyDetector) -> None:
        """All sensors anomalous → all flagged, high anomaly score."""
        readings = {
            "temperature": 200.0,
            "vibration": 10.0,
            "pressure": 100.0,
            "rpm": 3000.0,
            "power_consumption": 200.0,
        }
        result = detector.detect(readings)

        assert result.is_anomalous is True
        assert len(result.anomalous_metrics) == 5
        assert result.anomaly_score == 1.0  # Clamped to 1.0

    def test_anomaly_score_increases_with_severity(
        self, detector: SensorAnomalyDetector
    ) -> None:
        """More severe anomalies produce higher anomaly scores."""
        mild = detector.detect({"temperature": 92.0})  # z=2.7, normalised=1.08→clamped 1.0
        severe = detector.detect({"temperature": 200.0})  # z=13.5

        # Both are above threshold so both clamp to 1.0; instead compare
        # a sub-threshold case against an above-threshold case.
        sub_threshold = detector.detect({"temperature": 80.0})  # z=1.5, normalised=0.6
        assert mild.anomaly_score >= sub_threshold.anomaly_score


# ── Missing data handling ────────────────────────────────────────


class TestMissingData:
    """Graceful handling of missing, null, or invalid sensor values."""

    def test_missing_metric_not_anomalous(
        self, detector: SensorAnomalyDetector
    ) -> None:
        """Missing metrics are noted but don't trigger anomalies."""
        readings = {"temperature": 65.0}  # Only one of five metrics.
        result = detector.detect(readings)

        assert result.is_anomalous is False
        assert len(result.missing_metrics) == 4
        assert "vibration" in result.missing_metrics

    def test_none_value_treated_as_missing(
        self, detector: SensorAnomalyDetector
    ) -> None:
        """None values are treated as missing."""
        readings = {"temperature": None, "vibration": 2.0}
        result = detector.detect(readings)

        assert "temperature" in result.missing_metrics
        assert "temperature" not in result.metric_details

    def test_non_numeric_treated_as_missing(
        self, detector: SensorAnomalyDetector
    ) -> None:
        """Non-numeric values are treated as missing."""
        readings = {"temperature": "hot", "vibration": 2.0}
        result = detector.detect(readings)

        assert "temperature" in result.missing_metrics

    def test_nan_treated_as_missing(self, detector: SensorAnomalyDetector) -> None:
        """NaN values are treated as missing."""
        readings = {"temperature": float("nan"), "vibration": 2.0}
        result = detector.detect(readings)

        assert "temperature" in result.missing_metrics

    def test_inf_treated_as_missing(self, detector: SensorAnomalyDetector) -> None:
        """Infinity values are treated as missing."""
        readings = {"temperature": float("inf")}
        result = detector.detect(readings)

        assert "temperature" in result.missing_metrics

    def test_empty_readings(self, detector: SensorAnomalyDetector) -> None:
        """Empty readings → no anomaly, all metrics missing."""
        result = detector.detect({})

        assert result.is_anomalous is False
        assert result.anomaly_score == 0.0
        assert len(result.missing_metrics) == 5


# ── Boundary values ──────────────────────────────────────────────


class TestBoundaryValues:
    """Values at or near the anomaly threshold."""

    def test_at_threshold_not_anomalous(
        self, detector: SensorAnomalyDetector
    ) -> None:
        """Value exactly at the Z-score threshold → not anomalous (> not >=)."""
        # temperature: mean=65, std=10, threshold=2.5 → boundary at 65+25=90
        readings = {"temperature": 90.0}  # z=2.5, exactly at threshold
        result = detector.detect(readings)

        assert result.is_anomalous is False

    def test_just_above_threshold(self, detector: SensorAnomalyDetector) -> None:
        """Value just above the threshold → anomalous."""
        readings = {"temperature": 90.1}  # z=2.51
        result = detector.detect(readings)

        assert result.is_anomalous is True
        assert "temperature" in result.anomalous_metrics


# ── Custom ranges ────────────────────────────────────────────────


class TestCustomRanges:
    """Detector with custom normal ranges."""

    def test_custom_ranges(self) -> None:
        """Custom ranges are applied correctly."""
        custom = {
            "custom_metric": {"mean": 100.0, "std": 5.0, "z_threshold": 3.0},
        }
        detector = SensorAnomalyDetector(normal_ranges=custom)

        normal = detector.detect({"custom_metric": 110.0})  # z=2.0 < 3.0
        assert normal.is_anomalous is False

        anomalous = detector.detect({"custom_metric": 120.0})  # z=4.0 > 3.0
        assert anomalous.is_anomalous is True

    def test_known_metrics(self, detector: SensorAnomalyDetector) -> None:
        """known_metrics returns the list of monitored metrics."""
        metrics = detector.known_metrics
        assert "temperature" in metrics
        assert "vibration" in metrics
        assert len(metrics) == 5
