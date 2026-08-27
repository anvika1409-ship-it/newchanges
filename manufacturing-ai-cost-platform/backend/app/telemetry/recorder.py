"""Telemetry recorder for AI execution runs.

Persists UsageEvent and linked CostEvent records directly to SQLite / PostgreSQL
via SQLAlchemy async session (DATABASE_SCHEMA.md sections 14 and 15).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.telemetry import CostEvent, UsageEvent
from app.orchestrator.plan import ExecutionPlan

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TelemetryRecorder:
    """Records usage and cost events into the database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_execution(
        self,
        *,
        plan: ExecutionPlan,
        outcome: str,
        result: Any = None,
        error_code: str | None = None,
        duration_ms: int | None = None,
    ) -> UsageEvent | None:
        """Persist a UsageEvent and associated CostEvent for the executed plan."""
        try:
            event_id = str(uuid.uuid4())
            status_map = {
                "success": "SUCCESS",
                "blocked": "BLOCKED",
                "downgraded": "DOWNGRADED",
                "error": "FAILURE",
                "no_model": "FAILURE",
            }
            db_status = status_map.get(outcome, "SUCCESS" if outcome == "success" else "FAILURE")

            input_tokens = getattr(result, "input_tokens", None)
            output_tokens = getattr(result, "output_tokens", None)
            total_tokens = getattr(result, "total_tokens", None)
            fallback_used = getattr(result, "fallback_used", False)
            quality_score = getattr(result, "quality_score", None)

            usage_event = UsageEvent(
                id=event_id,
                request_id=plan.request_id,
                trace_id=plan.trace_id,
                tenant_id=plan.tenant_id,
                user_id=None,
                plant_id=plan.plant_id,
                department_id=plan.department_id,
                workload_id=plan.workload_id,
                agent_id=plan.selected_agent_id,
                model_id=plan.selected_model_id,
                timestamp=_utcnow(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                context_tokens=plan.max_context_tokens,
                image_count=getattr(result, "image_count", 0),
                tool_calls=plan.max_tool_calls,
                execution_time_ms=duration_ms,
                status=db_status,
                error_code=error_code,
                fallback_used=fallback_used,
                quality_score=quality_score,
                business_priority=str(plan.business_priority),
                risk_level=str(plan.risk_level),
                routing_policy_version=plan.routing_policy_version,
                budget_decision=str(plan.budget_status) if plan.budget_status else None,
            )
            self._session.add(usage_event)

            # Create linked CostEvent if cost or plan estimated cost is known
            cost_amount = getattr(result, "cost_amount", None)
            cost_provenance = getattr(result, "cost_provenance", "UNAVAILABLE")
            currency = getattr(result, "cost_currency", None) or plan.estimated_cost_currency or "USD"

            if cost_provenance not in ("ACTUAL", "ESTIMATED", "UNAVAILABLE"):
                cost_provenance = "UNAVAILABLE"

            cost_event = CostEvent(
                id=str(uuid.uuid4()),
                usage_event_id=event_id,
                estimated_cost=plan.estimated_cost,
                actual_cost=cost_amount if cost_provenance == "ACTUAL" else None,
                currency=currency,
                provenance=cost_provenance,
            )
            self._session.add(cost_event)

            await self._session.flush()
            return usage_event
        except Exception:
            logger.exception("telemetry_persistence_failed", extra={"request_id": plan.request_id})
            return None
