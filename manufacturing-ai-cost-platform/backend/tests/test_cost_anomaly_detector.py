"""Unit tests for CostAnomalyDetector.

Tests cover:
- Cost spikes
- Token spikes
- Latency spikes
- Unusual workload volume
- Unusual model usage
- Severity levels (LOW, MEDIUM, HIGH, CRITICAL)
- Normal steady state (zero anomalies)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.db.models.analytics import AnomalySeverity
from app.intelligence.cost_anomaly_detector import CostAnomalyDetector, DetectedAnomaly


@pytest.fixture
def detector() -> CostAnomalyDetector:
    return CostAnomalyDetector()


class TestCostAnomalyDetector:
    """Test suite for statistical anomaly detection."""

    def test_normal_metrics_produce_no_anomalies(self, detector: CostAnomalyDetector) -> None:
        """Metrics near mean produce no anomaly flags."""
        metrics = {
            "cost_usd": 52.0,  # baseline mean=50, std=10 -> z=0.2
            "token_count": 10500,  # baseline mean=10000, std=2000 -> z=0.25
            "latency_ms": 460.0,  # baseline mean=450, std=100 -> z=0.1
            "request_count": 105,  # baseline mean=100, std=20 -> z=0.25
        }
        baseline = {
            "cost_usd": {"mean": 50.0, "std": 10.0},
            "token_count": {"mean": 10000.0, "std": 2000.0},
            "latency_ms": {"mean": 450.0, "std": 100.0},
            "request_count": {"mean": 100.0, "std": 20.0},
        }

        anomalies = detector.detect_anomalies(
            current_metrics=metrics,
            historical_baseline=baseline,
        )

        assert len(anomalies) == 0

    def test_cost_spike_detection(self, detector: CostAnomalyDetector) -> None:
        """Severe spend increase triggers cost_spike anomaly."""
        metrics = {
            "cost_usd": 250.0,  # baseline mean=50, std=10 -> z=20.0 >> 2.5
        }
        baseline = {"cost_usd": {"mean": 50.0, "std": 10.0}}

        anomalies = detector.detect_anomalies(
            current_metrics=metrics,
            historical_baseline=baseline,
        )

        assert len(anomalies) == 1
        anom = anomalies[0]
        assert anom.anomaly_type == "cost_spike"
        assert anom.severity == AnomalySeverity.CRITICAL
        assert anom.deviation_percent == 400.0
        assert anom.actual_value == 250.0

    def test_token_spike_detection(self, detector: CostAnomalyDetector) -> None:
        """Surge in token consumption triggers token_spike anomaly."""
        metrics = {
            "token_count": 50000.0,  # baseline mean=10000, std=2000 -> z=20.0
        }
        baseline = {"token_count": {"mean": 10000.0, "std": 2000.0}}

        anomalies = detector.detect_anomalies(
            current_metrics=metrics,
            historical_baseline=baseline,
        )

        assert len(anomalies) == 1
        anom = anomalies[0]
        assert anom.anomaly_type == "token_spike"
        assert anom.actual_value == 50000.0

    def test_latency_spike_detection(self, detector: CostAnomalyDetector) -> None:
        """High latency response triggers latency_spike."""
        metrics = {
            "latency_ms": 1500.0,  # baseline mean=450, std=100 -> z=10.5 > 3.0
        }
        baseline = {"latency_ms": {"mean": 450.0, "std": 100.0}}

        anomalies = detector.detect_anomalies(
            current_metrics=metrics,
            historical_baseline=baseline,
        )

        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "latency_spike"

    def test_unusual_workload_volume(self, detector: CostAnomalyDetector) -> None:
        """Throughput burst triggers unusual_workload_volume."""
        metrics = {
            "request_count": 500.0,  # baseline mean=100, std=20 -> z=20.0
        }
        baseline = {"request_count": {"mean": 100.0, "std": 20.0}}

        anomalies = detector.detect_anomalies(
            current_metrics=metrics,
            historical_baseline=baseline,
        )

        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "unusual_workload_volume"

    def test_unusual_model_usage(self, detector: CostAnomalyDetector) -> None:
        """Sudden heavy shift to expensive models triggers unusual_model_usage."""
        metrics = {
            "model_distribution": {
                "claude-3-5-sonnet": 80,
                "gpt-4o-mini": 20,
            }
        }

        anomalies = detector.detect_anomalies(
            current_metrics=metrics,
            historical_baseline={},
        )

        assert len(anomalies) == 1
        anom = anomalies[0]
        assert anom.anomaly_type == "unusual_model_usage"
        assert "claude-3-5-sonnet" in anom.reason

    def test_scope_and_timestamp_forwarded(self, detector: CostAnomalyDetector) -> None:
        """Custom scope and timestamp are preserved in the detected anomaly."""
        custom_time = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)
        metrics = {"cost_usd": 200.0}
        baseline = {"cost_usd": {"mean": 50.0, "std": 10.0}}

        anomalies = detector.detect_anomalies(
            current_metrics=metrics,
            historical_baseline=baseline,
            scope_type="WORKLOAD",
            scope_id="pdm_agent",
            timestamp=custom_time,
        )

        assert len(anomalies) == 1
        anom = anomalies[0]
        assert anom.scope_type == "WORKLOAD"
        assert anom.scope_id == "pdm_agent"
        assert anom.timestamp == custom_time
