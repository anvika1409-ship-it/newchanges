"""Telemetry persistence for AI executions.

Writes one ``UsageEvent`` and one linked ``CostEvent`` per execution
(DATABASE_SCHEMA.md sections 14 and 15). Every cost-affecting AI execution must
produce telemetry (AI_DEVELOPMENT_RULES.md section 8), including the ones that
were refused — a budget block or a missing compatible model is a decision worth
recording, and a registry that only records successes cannot explain spend.

Two rules govern what lands in a column:

* **A limit is not a measurement.** ``max_context_tokens`` and
  ``max_tool_calls`` are ceilings the orchestrator applied. Recording them as
  ``context_tokens`` and ``tool_calls`` would report a budget as consumption and
  overstate usage in every aggregate built on top.
* **Unknown stays NULL.** A value the provider did not report is not defaulted
  to zero. Zero is a measurement; absence is not
  (AI_DEVELOPMENT_RULES.md section 10).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.models.telemetry import CostEvent, UsageEvent
from app.orchestrator.plan import ExecutionPlan

logger = get_logger(__name__)

#: Orchestrator outcome -> usage_events.status.
_STATUS_BY_OUTCOME: dict[str, str] = {
    "success": "SUCCESS",
    "blocked": "BLOCKED",
    "no_model": "FAILURE",
    "error": "FAILURE",
}

_VALID_PROVENANCE = ("ACTUAL", "ESTIMATED", "UNAVAILABLE")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_int(value: Any) -> int | None:
    """Coerce to int, preserving None. Booleans are not counts."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TelemetryRecorder:
    """Persists usage and cost events for one execution.

    Takes a session *factory*, not a session, and commits each record in its own
    transaction. This is deliberate and load-bearing: the request's session is
    rolled back when a request fails, and the executions most worth explaining —
    a budget block, a missing compatible model, a gateway failure — all fail. A
    recorder sharing that session would have its rows discarded by the very
    outcomes it exists to record.
    """

    def __init__(self, session_factory: Any) -> None:
        #: Callable returning an async context manager yielding a session.
        #: ``Database.session`` satisfies this.
        self._session_factory = session_factory

    async def record_execution(
        self,
        *,
        plan: ExecutionPlan,
        outcome: str,
        result: Any = None,
        error_code: str | None = None,
        duration_ms: float | None = None,
    ) -> UsageEvent | None:
        """Write the usage and cost events for an execution.

        Returns the persisted ``UsageEvent``, or ``None`` if persistence failed.

        Failure is logged and swallowed deliberately: telemetry is a record of
        work already done, and losing the record must not also lose the caller's
        result. This is the one place a broad ``except`` is justified, and it is
        never silent (AI_DEVELOPMENT_RULES.md section 26).
        """
        try:
            usage_event = self._build_usage_event(
                plan=plan,
                outcome=outcome,
                result=result,
                error_code=error_code,
                duration_ms=duration_ms,
            )
            async with self._session_factory() as session:
                session.add(usage_event)
                session.add(self._build_cost_event(usage_event.id, plan, result))
                await session.flush()
            return usage_event
        except Exception:
            logger.exception(
                "telemetry_persistence_failed",
                extra={"request_id": plan.request_id, "outcome": outcome},
            )
            return None

    # ------------------------------------------------------------- usage
    def _build_usage_event(
        self,
        *,
        plan: ExecutionPlan,
        outcome: str,
        result: Any,
        error_code: str | None,
        duration_ms: float | None,
    ) -> UsageEvent:
        return UsageEvent(
            id=str(uuid.uuid4()),
            # --- correlation (ARCHITECTURE.md section 15) -----------------
            request_id=plan.request_id,
            trace_id=plan.trace_id,
            tenant_id=plan.tenant_id,
            user_id=plan.user_id,
            # --- scope ----------------------------------------------------
            plant_id=plan.plant_id,
            department_id=plan.department_id,
            workload_id=plan.workload_id,
            agent_id=plan.selected_agent_id,
            model_id=plan.selected_model_id,
            timestamp=_utcnow(),
            # --- token usage: absent stays absent, never zeroed -----------
            input_tokens=_as_int(getattr(result, "input_tokens", None)),
            output_tokens=_as_int(getattr(result, "output_tokens", None)),
            total_tokens=_as_int(getattr(result, "total_tokens", None)),
            # Measured consumption, not plan.max_context_tokens, which is the
            # ceiling the orchestrator imposed.
            context_tokens=_as_int(getattr(result, "context_tokens", None)),
            image_count=_as_int(getattr(result, "image_count", None)),
            # Tool calls actually made, not plan.max_tool_calls.
            tool_calls=_as_int(getattr(result, "tool_calls", None)),
            # --- latency --------------------------------------------------
            execution_time_ms=_as_int(duration_ms),
            model_latency_ms=_as_int(getattr(result, "model_latency_ms", None)),
            # --- outcome --------------------------------------------------
            status=_STATUS_BY_OUTCOME.get(outcome, "FAILURE"),
            error_code=error_code,
            retry_count=_as_int(getattr(result, "retry_count", None)),
            fallback_used=bool(getattr(result, "fallback_used", False)),
            quality_score=getattr(result, "quality_score", None),
            # --- decisions ------------------------------------------------
            business_priority=str(plan.business_priority),
            risk_level=str(plan.risk_level),
            routing_policy_version=plan.routing_policy_version,
            budget_decision=str(plan.budget_status) if plan.budget_status else None,
            guardrail_decision=getattr(result, "guardrail_decision", None),
        )

    # -------------------------------------------------------------- cost
    def _build_cost_event(
        self, usage_event_id: str, plan: ExecutionPlan, result: Any
    ) -> CostEvent:
        """Build the linked cost event.

        Written even when nothing could be costed. An execution with no
        computable cost is counted as ``unavailable`` by the aggregation layer,
        which is how unpriced spend stays visible instead of vanishing.

        ``actual_cost`` is populated only when provenance is ACTUAL. The two
        columns are summed independently by provenance, so writing a value into
        the wrong one would double-count it.
        """
        provenance = getattr(result, "cost_provenance", None) or "UNAVAILABLE"
        if provenance not in _VALID_PROVENANCE:
            logger.warning(
                "telemetry_unknown_cost_provenance",
                extra={"provenance": provenance, "request_id": plan.request_id},
            )
            provenance = "UNAVAILABLE"

        amount = getattr(result, "cost_amount", None)
        currency = (
            getattr(result, "cost_currency", None) or plan.estimated_cost_currency
        )

        return CostEvent(
            id=str(uuid.uuid4()),
            usage_event_id=usage_event_id,
            estimated_cost=amount if provenance == "ESTIMATED" else plan.estimated_cost,
            actual_cost=amount if provenance == "ACTUAL" else None,
            currency=currency,
            provenance=provenance,
        )
