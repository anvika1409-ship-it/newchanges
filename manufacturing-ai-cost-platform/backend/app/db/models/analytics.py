"""ORM mappings for forecasts and anomalies tables.

Implements DATABASE_SCHEMA.md sections 16 and 17:
- forecasts: Stores forecasted AI costs across scopes and horizons.
- anomalies: Stores detected metric, cost, latency, token, and volume anomalies.
"""

from __future__ import annotations

from enum import StrEnum

from app.db.models.intelligence import Anomaly, Forecast


class AnomalySeverity(StrEnum):
    """Severity classification for anomalies (API_CONTRACT.yaml)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AnomalyStatus(StrEnum):
    """Resolution status of an anomaly record."""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


# Aliases mapped to the canonical schema models in app.db.models.intelligence
ForecastRecord = Forecast
AnomalyRecord = Anomaly
