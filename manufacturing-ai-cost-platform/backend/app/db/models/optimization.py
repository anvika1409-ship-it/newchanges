"""ORM mapping for optimization_recommendations table.

Implements DATABASE_SCHEMA.md section 18:
Stores generated optimization candidates, estimated savings, impact metrics,
and proposed policy references with pending approval status.
"""

from __future__ import annotations

from enum import StrEnum

from app.db.models.intelligence import OptimizationRecommendation


class OptimizationStatus(StrEnum):
    """Recommendation lifecycle status (API_CONTRACT.yaml)."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"


class OptimizationRiskLevel(StrEnum):
    """Risk classification for optimization actions."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# Alias mapped to the canonical schema model in app.db.models.intelligence
OptimizationRecommendationRecord = OptimizationRecommendation
