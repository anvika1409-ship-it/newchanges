"""Model gateway telemetry.

Every cost-affecting AI execution must produce telemetry
(AI_DEVELOPMENT_RULES.md section 8). This module defines the record the gateway
emits for each call and the sink abstraction that receives it.

What is deliberately **not** here:

* **Cost.** The gateway has no pricing. Pricing is registry metadata
  (ARCHITECTURE.md section 8), and cost is computed by the cost layer from these
  token counts plus that metadata.
* **Prompt or response content.** Never recorded (SECURITY.md section 17). The
  record carries shapes and sizes so a call can be correlated and sized without
  exposing what was in it.
* **Credentials.** Never present in any field.

The record maps onto ``usage_events`` in DATABASE_SCHEMA.md section 14. A
database-backed sink slots in behind ``TelemetrySink`` once the ORM models land.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.integrations.llm.interface import TokenUsage, UsageProvenance

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GatewayCallTelemetry:
    """One model gateway call, successful or not."""

    provider: str
    operation: str
    model: str

    #: "success" or "error".
    outcome: str

    #: Total wall-clock time across every attempt, including backoff waits.
    duration_ms: float

    #: Time spent in the successful provider call alone. None when all failed.
    model_latency_ms: float | None = None

    attempts: int = 1
    retry_count: int = 0

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    usage_provenance: str = UsageProvenance.UNAVAILABLE.value

    #: Normalized error code from errors.py. None on success.
    error_code: str | None = None

    #: True when the circuit breaker refused the call outright.
    circuit_open: bool = False

    request_id: str | None = None
    trace_id: str | None = None

    #: Non-sensitive shape summary: counts and sizes only, never content.
    request_shape: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_usage(
        cls,
        usage: TokenUsage,
        **kwargs: Any,
    ) -> GatewayCallTelemetry:
        """Build a record from reported usage.

        Counts stay None when the provider reported none. They are never
        defaulted to zero: zero is a measurement, absence is not.
        """
        return cls(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            usage_provenance=usage.provenance.value,
            **kwargs,
        )


@runtime_checkable
class TelemetrySink(Protocol):
    """Receives one record per gateway call.

    Implementations must not raise. A telemetry failure must never fail the
    business call that produced it.
    """

    async def record(self, telemetry: GatewayCallTelemetry) -> None: ...


class LoggingTelemetrySink:
    """Default sink: emits one structured log line per call.

    Every field is safe to log. The record carries no content and no secrets.
    """

    async def record(self, telemetry: GatewayCallTelemetry) -> None:
        payload = telemetry.as_dict()
        shape = payload.pop("request_shape", {})
        payload.update({f"request_{k}": v for k, v in shape.items()})

        if telemetry.outcome == "success":
            logger.info("model_gateway_call", extra=payload)
        else:
            logger.warning("model_gateway_call_failed", extra=payload)


class NullTelemetrySink:
    """Discards records. For unit tests that assert on something else."""

    async def record(self, telemetry: GatewayCallTelemetry) -> None:
        return None


class CollectingTelemetrySink:
    """Keeps records in memory so tests can assert on what was emitted."""

    def __init__(self) -> None:
        self.records: list[GatewayCallTelemetry] = []

    async def record(self, telemetry: GatewayCallTelemetry) -> None:
        self.records.append(telemetry)

    @property
    def last(self) -> GatewayCallTelemetry | None:
        return self.records[-1] if self.records else None


async def emit(sink: TelemetrySink, telemetry: GatewayCallTelemetry) -> None:
    """Emit a record, swallowing sink failures.

    The one place ``except Exception`` is justified: a broken telemetry sink
    must not turn a successful model call into a failed request. The failure is
    logged rather than silently dropped (AI_DEVELOPMENT_RULES.md section 26).
    """
    try:
        await sink.record(telemetry)
    except Exception:
        logger.exception("telemetry_sink_failed", extra={"provider": telemetry.provider})
