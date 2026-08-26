"""Repository for anomaly persistence and querying.

Keeps SQLite queries behind SQLAlchemy abstractions (AI_DEVELOPMENT_RULES.md section 16).
"""

from __future__ import annotations

from app.repositories.forecast_repository import AnomalyRepository

__all__ = ["AnomalyRepository"]
