"""Telemetry event emitter.

Every AI execution must emit telemetry events for observability, debugging and
compliance (AI_DEVELOPMENT_RULES.md section 8, ARCHITECTURE.md section 15).

This lightweight in-memory implementation is the MVP telemetry sink.  Events are
structured dicts suitable for forwarding to an external collector once one is
integrated.  The emitter never records secrets — callers are responsible for
excluding API keys, tokens and PII from event ``data``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.context import get_request_id, get_trace_id
from app.core.logging import get_logger

logger = get_logger(__name__)

# Recognised event types (AI_WORKFLOWS.md section 9).
VALID_EVENT_TYPES = frozenset(
    {
        "workflow.started",
        "workflow.completed",
        "workflow.failed",
        "workflow.step_completed",
        "llm.call_started",
        "llm.call_completed",
        "llm.call_failed",
        "cost.recorded",
    }
)


class TelemetryEmitter:
    """In-memory telemetry sink that records structured events.

    Events are held in ``_events`` so tests can assert on the telemetry produced
    during a workflow run.  In production this would forward to OpenTelemetry or
    a similar collector.
    """

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def emit(
        self,
        event_type: str,
        execution_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record a telemetry event.

        Args:
            event_type: One of the recognised event types.
            execution_id: Correlation identifier for the AI execution.
            data: Event-specific payload.  Must not contain secrets.
        """
        if event_type not in VALID_EVENT_TYPES:
            logger.warning(
                "unknown_telemetry_event_type",
                extra={"event_type": event_type, "execution_id": execution_id},
            )

        event: dict[str, Any] = {
            "event_type": event_type,
            "execution_id": execution_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": get_request_id(),
            "trace_id": get_trace_id(),
            "data": data or {},
        }

        self._events.append(event)
        logger.info(
            "telemetry_event",
            extra={"event_type": event_type, "execution_id": execution_id},
        )

    def get_events(self, execution_id: str | None = None) -> list[dict[str, Any]]:
        """Return recorded events, optionally filtered by execution ID."""
        if execution_id is not None:
            return [e for e in self._events if e["execution_id"] == execution_id]
        return list(self._events)

    def clear(self) -> None:
        """Remove all recorded events."""
        self._events.clear()
