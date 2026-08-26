"""Background analytics worker for asynchronous cost forecasting and anomaly detection.

Keeps heavy analytical and forecasting computation decoupled from the
synchronous API request path (AI_DEVELOPMENT_RULES.md sections 17 and 31).

Can be scheduled via cron, triggered by event consumers, or run as background tasks.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.models.analytics import AnomalyRecord, ForecastRecord
from app.intelligence.cost_anomaly_detector import CostAnomalyDetector, DetectedAnomaly
from app.intelligence.cost_forecaster import CostForecaster, ForecastResult
from app.repositories.anomaly_repository import AnomalyRepository
from app.repositories.forecast_repository import ForecastRepository

logger = get_logger(__name__)


class CostAnalyticsWorker:
    """Asynchronous background worker for cost analytics."""

    def __init__(
        self,
        *,
        forecast_repository: ForecastRepository | None = None,
        anomaly_repository: AnomalyRepository | None = None,
        forecaster: CostForecaster | None = None,
        anomaly_detector: CostAnomalyDetector | None = None,
    ) -> None:
        self._forecast_repo = forecast_repository
        self._anomaly_repo = anomaly_repository
        self._forecaster = forecaster or CostForecaster()
        self._detector = anomaly_detector or CostAnomalyDetector()

    async def run_forecasting_job(
        self,
        historical_daily_costs: list[dict[str, Any]] | list[tuple[date, float]],
        *,
        scope_type: str = "TENANT",
        scope_id: str = "default",
        tenant_id: str | None = None,
        horizon_days: int = 30,
        start_date: date | None = None,
    ) -> ForecastResult:
        """Run asynchronous cost forecasting computation and persist records."""
        logger.info(
            "forecasting_job_started",
            extra={"scope_type": scope_type, "scope_id": scope_id, "horizon_days": horizon_days},
        )

        result = self._forecaster.generate_forecast(
            historical_daily_costs=historical_daily_costs,
            horizon_days=horizon_days,
            start_date=start_date,
        )

        # Persist forecast points if repository is available
        if self._forecast_repo is not None:
            records = [
                ForecastRecord(
                    tenant_id=tenant_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    forecast_date=p.forecast_date,
                    predicted_cost=p.predicted_cost,
                    lower_bound=p.lower_bound,
                    upper_bound=p.upper_bound,
                    confidence=p.confidence,
                    forecast_model_name=result.forecast_model_name,
                    forecast_model_version=result.forecast_model_version,
                )
                for p in result.daily_points
            ]
            await self._forecast_repo.create_many(records)
            logger.info("forecasts_persisted", extra={"record_count": len(records)})

        return result

    async def run_anomaly_detection_job(
        self,
        current_metrics: dict[str, Any],
        *,
        historical_baseline: dict[str, Any] | None = None,
        scope_type: str = "TENANT",
        scope_id: str = "default",
        tenant_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> list[DetectedAnomaly]:
        """Run asynchronous anomaly detection and persist detected anomalies."""
        ts = timestamp or datetime.now(UTC)
        logger.info(
            "anomaly_detection_job_started",
            extra={"scope_type": scope_type, "scope_id": scope_id},
        )

        anomalies = self._detector.detect_anomalies(
            current_metrics=current_metrics,
            historical_baseline=historical_baseline,
            scope_type=scope_type,
            scope_id=scope_id,
            timestamp=ts,
        )

        # Persist detected anomalies if repository is available
        if self._anomaly_repo is not None and anomalies:
            eff_tenant_id = tenant_id or "tenant-1"
            records = [
                AnomalyRecord(
                    tenant_id=eff_tenant_id,
                    timestamp=a.timestamp or ts,
                    scope_type=a.scope_type,
                    scope_id=a.scope_id,
                    anomaly_type=a.anomaly_type,
                    severity=a.severity,
                    expected_value=a.expected_value,
                    actual_value=a.actual_value,
                    deviation_percent=a.deviation_percent,
                    reason=a.reason,
                    status=a.status,
                )
                for a in anomalies
            ]
            await self._anomaly_repo.create_many(records)
            logger.info("anomalies_persisted", extra={"count": len(records)})

        return anomalies
