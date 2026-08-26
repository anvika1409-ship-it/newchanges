"""Statistical anomaly detection for machine sensor data.

Anomaly detection uses ML/statistics, never an LLM
(AI_WORKFLOWS.md section 1, AI_DEVELOPMENT_RULES.md section 7).

The detector applies Z-score analysis against configurable per-metric
normal ranges.  This is a lightweight, deterministic approach that avoids
the latency and cost of an LLM call for what is fundamentally a numerical
threshold check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# Default operating ranges derived from typical industrial equipment.
# These are configurable defaults, NOT hard-coded model pricing or GenAILab
# behaviour (AI_DEVELOPMENT_RULES.md section 2).
NORMAL_RANGES: dict[str, dict[str, float]] = {
    "temperature": {"mean": 65.0, "std": 10.0, "z_threshold": 2.5},
    "vibration": {"mean": 2.0, "std": 0.8, "z_threshold": 2.0},
    "pressure": {"mean": 30.0, "std": 5.0, "z_threshold": 2.5},
    "rpm": {"mean": 1500.0, "std": 200.0, "z_threshold": 2.5},
    "power_consumption": {"mean": 75.0, "std": 15.0, "z_threshold": 2.5},
}


@dataclass(frozen=True, slots=True)
class MetricDetail:
    """Per-metric anomaly analysis result."""

    value: float
    z_score: float
    z_threshold: float
    is_anomalous: bool
    mean: float
    std: float


@dataclass(frozen=True, slots=True)
class AnomalyResult:
    """Complete anomaly detection result.

    ``data_quality`` is ACTUAL because this result is computed deterministically
    from measured sensor data (AI_DEVELOPMENT_RULES.md section 10).
    """

    is_anomalous: bool
    anomaly_score: float  # 0.0–1.0
    anomalous_metrics: tuple[str, ...] = ()
    metric_details: dict[str, MetricDetail] = field(default_factory=dict)
    missing_metrics: tuple[str, ...] = ()
    data_quality: str = "ACTUAL"


class SensorAnomalyDetector:
    """Z-score based anomaly detector for machine sensor readings.

    Each metric is compared against its normal range.  A metric is flagged as
    anomalous when its absolute Z-score exceeds the configured threshold.

    The overall anomaly score is the maximum normalised deviation across all
    metrics, clamped to [0.0, 1.0].  The machine is flagged as anomalous when
    any single metric exceeds its threshold.

    Missing sensor readings are tracked but do not trigger anomalies.
    """

    def __init__(
        self,
        normal_ranges: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self._ranges = normal_ranges or NORMAL_RANGES

    def detect(self, sensor_readings: dict[str, Any]) -> AnomalyResult:
        """Run anomaly detection on a set of sensor readings.

        Args:
            sensor_readings: Dict mapping metric names to numeric values.
                             Missing or non-numeric values are noted as missing.

        Returns:
            ``AnomalyResult`` with per-metric details.
        """
        metric_details: dict[str, MetricDetail] = {}
        anomalous_metrics: list[str] = []
        missing_metrics: list[str] = []
        max_normalised_deviation: float = 0.0

        for metric_name, params in self._ranges.items():
            raw_value = sensor_readings.get(metric_name)

            # Handle missing or non-numeric readings.
            if raw_value is None or not isinstance(raw_value, (int, float)):
                missing_metrics.append(metric_name)
                continue

            if math.isnan(raw_value) or math.isinf(raw_value):
                missing_metrics.append(metric_name)
                continue

            value = float(raw_value)
            mean = params["mean"]
            std = params["std"]
            z_threshold = params["z_threshold"]

            # Guard against zero std (would cause division by zero).
            if std <= 0:
                z_score = 0.0
            else:
                z_score = abs(value - mean) / std

            is_metric_anomalous = z_score > z_threshold

            detail = MetricDetail(
                value=value,
                z_score=round(z_score, 4),
                z_threshold=z_threshold,
                is_anomalous=is_metric_anomalous,
                mean=mean,
                std=std,
            )
            metric_details[metric_name] = detail

            if is_metric_anomalous:
                anomalous_metrics.append(metric_name)

            # Normalised deviation: z_score / z_threshold gives 1.0 at threshold.
            if z_threshold > 0:
                normalised = z_score / z_threshold
                max_normalised_deviation = max(max_normalised_deviation, normalised)

        # Anomaly score: 0.0 = perfectly normal, 1.0 = at or beyond threshold.
        anomaly_score = min(max_normalised_deviation, 1.0)

        return AnomalyResult(
            is_anomalous=len(anomalous_metrics) > 0,
            anomaly_score=round(anomaly_score, 4),
            anomalous_metrics=tuple(anomalous_metrics),
            metric_details=metric_details,
            missing_metrics=tuple(missing_metrics),
            data_quality="ACTUAL",
        )

    @property
    def known_metrics(self) -> list[str]:
        """Return the list of metrics this detector monitors."""
        return list(self._ranges.keys())
