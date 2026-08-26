"""Cost tracking for AI executions.

All LLM usage must be tracked with token counts and costs
(AI_DEVELOPMENT_RULES.md section 8, DATABASE_SCHEMA.md section 15).

Cost provenance is strictly maintained: ``ACTUAL`` when the provider reports
real usage, ``ESTIMATED`` when inferred from token counts and published rates,
``UNAVAILABLE`` when neither is possible.  Costs of different provenance are
never summed interchangeably (API_CONTRACT.yaml).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class CostType(StrEnum):
    """Valid cost types from DATABASE_SCHEMA.md."""

    LLM_INFERENCE = "llm_inference"
    EMBEDDING = "embedding"
    DATA_PROCESSING = "data_processing"
    INFRASTRUCTURE = "infrastructure"


class CostProvenance(StrEnum):
    """Cost provenance markers (API_CONTRACT.yaml, AI_DEVELOPMENT_RULES.md section 10)."""

    ACTUAL = "ACTUAL"
    ESTIMATED = "ESTIMATED"
    UNAVAILABLE = "UNAVAILABLE"


class CostTracker:
    """In-memory cost tracker for AI executions.

    Records cost events and provides aggregation helpers.  In production the
    records would be persisted to the ``cost_events`` table.
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def record_cost(
        self,
        execution_id: str,
        cost_type: str,
        amount_usd: float,
        description: str,
        provenance: str = CostProvenance.ESTIMATED,
    ) -> None:
        """Record a cost event.

        Args:
            execution_id: Associated execution ID.
            cost_type: Category of cost (must be a ``CostType`` value).
            amount_usd: Cost amount in USD.
            description: Human-readable description.
            provenance: One of ACTUAL, ESTIMATED, UNAVAILABLE.
        """
        record: dict[str, Any] = {
            "execution_id": execution_id,
            "cost_type": cost_type,
            "amount_usd": amount_usd,
            "description": description,
            "provenance": provenance,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        self._records.append(record)
        logger.info(
            "cost_recorded",
            extra={
                "execution_id": execution_id,
                "cost_type": cost_type,
                "amount_usd": amount_usd,
                "provenance": provenance,
            },
        )

    def get_total_cost(self, execution_id: str) -> float:
        """Get total cost for an execution (all provenances)."""
        return sum(
            r["amount_usd"]
            for r in self._records
            if r["execution_id"] == execution_id
        )

    def get_records(self, execution_id: str | None = None) -> list[dict[str, Any]]:
        """Get cost records, optionally filtered by execution ID."""
        if execution_id is not None:
            return [r for r in self._records if r["execution_id"] == execution_id]
        return list(self._records)

    def clear(self) -> None:
        """Remove all recorded cost events."""
        self._records.clear()
