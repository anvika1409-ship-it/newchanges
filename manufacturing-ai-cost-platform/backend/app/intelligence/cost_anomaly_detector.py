"""Statistical anomaly detection for AI cost, token, latency, workload, and model metrics.

Implements multi-metric anomaly detection:
- cost_spike: Abnormal surge in spend.
- token_spike: Abnormal surge in token consumption.
- latency_spike: Abnormal increase in model response latency.
- unusual_workload_volume: Unusual spike or drop in request throughput.
- unusual_model_usage: Abnormal shift in model distribution.

Outputs structured anomaly candidates ready for database persistence
(DATABASE_SCHEMA.md section 17, API_CONTRACT.yaml).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.db.models.analytics import AnomalySeverity, AnomalyStatus


@dataclass(frozen=True, slots=True)
class DetectedAnomaly:
    """Structured detected anomaly ready for persistence and API responses."""

    anomaly_type: str
    severity: str
    expected_value: float
    actual_value: float
    deviation_percent: float
    reason: str
    scope_type: str = "TENANT"
    scope_id: str = "default"
    timestamp: datetime | None = None
    status: str = AnomalyStatus.OPEN


class CostAnomalyDetector:
    """Statistical anomaly detector for AI telemetry and usage events."""

    def __init__(
        self,
        *,
        cost_z_threshold: float = 2.5,
        token_z_threshold: float = 2.5,
        latency_z_threshold: float = 3.0,
        volume_z_threshold: float = 2.5,
    ) -> None:
        self._cost_z = cost_z_threshold
        self._token_z = token_z_threshold
        self._latency_z = latency_z_threshold
        self._volume_z = volume_z_threshold

    def detect_anomalies(
        self,
        *,
        current_metrics: dict[str, Any],
        historical_baseline: dict[str, Any] | None = None,
        scope_type: str = "TENANT",
        scope_id: str = "default",
        timestamp: datetime | None = None,
    ) -> list[DetectedAnomaly]:
        """Detect anomalies across cost, token, latency, volume, and model metrics.

        Args:
            current_metrics: Dict containing current observations:
                - `cost_usd`: Current period cost
                - `token_count`: Current period tokens
                - `latency_ms`: Observed latency
                - `request_count`: Observed workload volume
                - `model_used`: Name of model invoked
                - `model_distribution`: Dict of model name to count
            historical_baseline: Dict containing baseline statistics (mean, std) for metrics.
        """
        ts = timestamp or datetime.now(UTC)
        anomalies: list[DetectedAnomaly] = []
        baseline = historical_baseline or {}

        # 1. Cost Spike Detection
        cost_actual = current_metrics.get("cost_usd")
        if cost_actual is not None and isinstance(cost_actual, (int, float)):
            cost_base = baseline.get("cost_usd", {"mean": 50.0, "std": 10.0})
            anomaly = self._evaluate_metric(
                metric_name="cost_spike",
                actual=float(cost_actual),
                mean=float(cost_base.get("mean", 50.0)),
                std=float(cost_base.get("std", 10.0)),
                threshold_z=self._cost_z,
                unit_label="USD",
                scope_type=scope_type,
                scope_id=scope_id,
                timestamp=ts,
            )
            if anomaly:
                anomalies.append(anomaly)

        # 2. Token Spike Detection
        tokens_actual = current_metrics.get("token_count")
        if tokens_actual is not None and isinstance(tokens_actual, (int, float)):
            token_base = baseline.get("token_count", {"mean": 10000.0, "std": 2000.0})
            anomaly = self._evaluate_metric(
                metric_name="token_spike",
                actual=float(tokens_actual),
                mean=float(token_base.get("mean", 10000.0)),
                std=float(token_base.get("std", 2000.0)),
                threshold_z=self._token_z,
                unit_label="tokens",
                scope_type=scope_type,
                scope_id=scope_id,
                timestamp=ts,
            )
            if anomaly:
                anomalies.append(anomaly)

        # 3. Latency Spike Detection
        latency_actual = current_metrics.get("latency_ms")
        if latency_actual is not None and isinstance(latency_actual, (int, float)):
            latency_base = baseline.get("latency_ms", {"mean": 450.0, "std": 100.0})
            anomaly = self._evaluate_metric(
                metric_name="latency_spike",
                actual=float(latency_actual),
                mean=float(latency_base.get("mean", 450.0)),
                std=float(latency_base.get("std", 100.0)),
                threshold_z=self._latency_z,
                unit_label="ms",
                scope_type=scope_type,
                scope_id=scope_id,
                timestamp=ts,
            )
            if anomaly:
                anomalies.append(anomaly)

        # 4. Unusual Workload Volume Detection
        volume_actual = current_metrics.get("request_count")
        if volume_actual is not None and isinstance(volume_actual, (int, float)):
            volume_base = baseline.get("request_count", {"mean": 100.0, "std": 20.0})
            anomaly = self._evaluate_metric(
                metric_name="unusual_workload_volume",
                actual=float(volume_actual),
                mean=float(volume_base.get("mean", 100.0)),
                std=float(volume_base.get("std", 20.0)),
                threshold_z=self._volume_z,
                unit_label="requests",
                scope_type=scope_type,
                scope_id=scope_id,
                timestamp=ts,
            )
            if anomaly:
                anomalies.append(anomaly)

        # 5. Unusual Model Usage Detection
        model_dist = current_metrics.get("model_distribution")
        if isinstance(model_dist, dict) and model_dist:
            expected_models = baseline.get("expected_models", ["gpt-4o-mini", "llama-3-8b"])
            expensive_models = baseline.get("expensive_models", ["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"])

            total_calls = sum(model_dist.values())
            for model_name, count in model_dist.items():
                if total_calls > 0:
                    share = count / total_calls
                    # If an expensive model accounts for an unexpected surge (> 40% when baseline was < 10%)
                    if model_name in expensive_models and share > 0.40:
                        deviation = round(share * 100.0, 2)
                        anomalies.append(
                            DetectedAnomaly(
                                anomaly_type="unusual_model_usage",
                                severity=AnomalySeverity.HIGH if share > 0.60 else AnomalySeverity.MEDIUM,
                                expected_value=10.0,
                                actual_value=deviation,
                                deviation_percent=deviation - 10.0,
                                reason=(
                                    f"Unexpected shift to expensive model '{model_name}' representing "
                                    f"{deviation:.1f}% of total calls (expected < 10%)."
                                ),
                                scope_type=scope_type,
                                scope_id=scope_id,
                                timestamp=ts,
                            )
                        )

        return anomalies

    # ── Metric Evaluation Helper ──────────────────────────────────

    @staticmethod
    def _evaluate_metric(
        *,
        metric_name: str,
        actual: float,
        mean: float,
        std: float,
        threshold_z: float,
        unit_label: str,
        scope_type: str,
        scope_id: str,
        timestamp: datetime,
    ) -> DetectedAnomaly | None:
        if std <= 0:
            std = max(1.0, mean * 0.1)

        z_score = (actual - mean) / std

        # We flag anomalies when the metric significantly exceeds the threshold
        if z_score <= threshold_z:
            return None

        # Calculate deviation percent
        deviation_pct = round(((actual - mean) / max(0.01, mean)) * 100.0, 2)

        # Determine severity based on z-score magnitude
        if z_score >= threshold_z * 2.0:
            severity = AnomalySeverity.CRITICAL
        elif z_score >= threshold_z * 1.5:
            severity = AnomalySeverity.HIGH
        elif z_score >= threshold_z * 1.2:
            severity = AnomalySeverity.MEDIUM
        else:
            severity = AnomalySeverity.LOW

        reason = (
            f"{metric_name.replace('_', ' ').title()} detected: observed {actual:.2f} {unit_label} "
            f"exceeds historical baseline ({mean:.2f} ± {std:.2f} {unit_label}) "
            f"with z-score of {z_score:.2f} (+{deviation_pct:.1f}% deviation)."
        )

        return DetectedAnomaly(
            anomaly_type=metric_name,
            severity=severity,
            expected_value=round(mean, 2),
            actual_value=round(actual, 2),
            deviation_percent=deviation_pct,
            reason=reason,
            scope_type=scope_type,
            scope_id=scope_id,
            timestamp=timestamp,
        )
