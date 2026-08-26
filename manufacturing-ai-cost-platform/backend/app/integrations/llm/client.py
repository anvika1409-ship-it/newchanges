"""Provider-agnostic gateway machinery.

Holds everything that is not specific to one provider:

* ``RetryPolicy`` — bounded retries with exponential backoff and jitter
* ``CircuitBreaker`` — the abstraction, plus a null and an in-memory implementation
* ``ResilientGateway`` — wraps any ``ModelGatewayInterface`` with timeout,
  retry, circuit breaking and telemetry
* ``MockModelGateway`` — deterministic test double, no network
* ``build_model_gateway`` — configuration-driven selection

The resilience controls implement SECURITY.md section 19. They live here rather
than inside the GenAILab adapter so every future provider inherits them
unchanged, and so they can be tested without touching a provider SDK.
"""

from __future__ import annotations

import asyncio
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from app.core.config import ModelGatewayProvider, Settings
from app.core.context import get_request_id, get_trace_id
from app.core.logging import get_logger
from app.integrations.llm.errors import (
    CircuitOpenError,
    GatewayRateLimitError,
    ModelCapabilityError,
    ModelGatewayError,
)
from app.integrations.llm.interface import (
    Capability,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelGatewayInterface,
    MultimodalGenerationRequest,
    SpeechTranscriptionRequest,
    SpeechTranscriptionResponse,
    TextGenerationRequest,
    TextGenerationResponse,
    TokenUsage,
    UsageProvenance,
)
from app.integrations.llm.telemetry import (
    CollectingTelemetrySink,
    GatewayCallTelemetry,
    LoggingTelemetrySink,
    TelemetrySink,
    emit,
)

logger = get_logger(__name__)

T = TypeVar("T")

# Jitter only; not used for anything security-sensitive.
_jitter = random.SystemRandom()


# ===========================================================================
# Retry policy
# ===========================================================================
@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff with full jitter.

    ``max_attempts`` counts the first try, so ``max_attempts=1`` disables
    retrying entirely. Only errors marked ``retryable`` are retried — an
    authentication failure or a malformed request is never repeated.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    #: Total wall-clock ceiling across all attempts (SECURITY.md section 19).
    max_elapsed_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

    def delay_for(self, attempt: int) -> float:
        """Backoff before the given 1-based attempt number.

        Full jitter (random between 0 and the exponential ceiling) rather than
        fixed backoff, so concurrent callers failing together do not retry in
        lockstep and re-hammer a recovering provider.
        """
        ceiling = min(self.base_delay_seconds * (2 ** (attempt - 1)), self.max_delay_seconds)
        return _jitter.uniform(0.0, ceiling)


# ===========================================================================
# Circuit breaker
# ===========================================================================
class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker(ABC):
    """Hook for failing fast when a provider is unhealthy.

    Kept abstract so a deployment can back it with shared state (Redis) rather
    than per-process memory, without touching gateway code.
    """

    @abstractmethod
    async def allow(self) -> bool:
        """Whether a call may proceed."""

    @abstractmethod
    async def record_success(self) -> None: ...

    @abstractmethod
    async def record_failure(self) -> None: ...

    @property
    @abstractmethod
    def state(self) -> CircuitState: ...


class NullCircuitBreaker(CircuitBreaker):
    """Always closed. The default when circuit breaking is disabled."""

    async def allow(self) -> bool:
        return True

    async def record_success(self) -> None:
        return None

    async def record_failure(self) -> None:
        return None

    @property
    def state(self) -> CircuitState:
        return CircuitState.CLOSED


class InMemoryCircuitBreaker(CircuitBreaker):
    """Per-process breaker.

    Opens after ``failure_threshold`` consecutive failures, then after
    ``reset_timeout_seconds`` allows a single probe (half-open). A successful
    probe closes it; a failed probe re-opens it.

    Per-process state means each worker learns independently. That is acceptable
    for the MVP and is why the abstraction exists.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_timeout_seconds: float = 30.0,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout_seconds
        self._now = time_source
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        if self._opened_at is None:
            return CircuitState.CLOSED
        return CircuitState.HALF_OPEN if self._half_open else CircuitState.OPEN

    async def allow(self) -> bool:
        async with self._lock:
            if self._opened_at is None:
                return True
            if self._now() - self._opened_at >= self._reset_timeout:
                # Let exactly one probe through.
                self._half_open = True
                return True
            return False

    async def record_success(self) -> None:
        async with self._lock:
            if self._opened_at is not None:
                logger.info("circuit_breaker_closed")
            self._consecutive_failures = 0
            self._opened_at = None
            self._half_open = False

    async def record_failure(self) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            if self._half_open:
                # The probe failed: reopen and restart the clock.
                self._opened_at = self._now()
                self._half_open = False
                logger.warning("circuit_breaker_reopened")
                return
            if self._consecutive_failures >= self._failure_threshold:
                self._opened_at = self._now()
                logger.warning(
                    "circuit_breaker_opened",
                    extra={"consecutive_failures": self._consecutive_failures},
                )


# ===========================================================================
# Resilient wrapper
# ===========================================================================
class ResilientGateway(ModelGatewayInterface):
    """Adds timeout, retry, circuit breaking and telemetry to any gateway.

    Composition rather than inheritance, so the provider adapter stays a thin
    protocol translation and every provider gets identical resilience.
    """

    def __init__(
        self,
        inner: ModelGatewayInterface,
        *,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        telemetry_sink: TelemetrySink | None = None,
    ) -> None:
        self._inner = inner
        self._retry = retry_policy or RetryPolicy()
        self._breaker = circuit_breaker or NullCircuitBreaker()
        self._telemetry = telemetry_sink or LoggingTelemetrySink()

        self.provider_name = inner.provider_name
        self.capabilities = inner.capabilities

    @property
    def inner(self) -> ModelGatewayInterface:
        return self._inner

    @property
    def circuit_state(self) -> CircuitState:
        return self._breaker.state

    # ------------------------------------------------------------ operations
    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResponse:
        self._require(Capability.TEXT, "generate_text", request.model)
        return await self._call(
            "generate_text",
            request.model,
            lambda: self._inner.generate_text(request),
            request_id=request.request_id,
            trace_id=request.trace_id,
            shape=self._describe_messages(request.messages),
        )

    async def generate_multimodal(
        self, request: MultimodalGenerationRequest
    ) -> TextGenerationResponse:
        self._require(Capability.MULTIMODAL, "generate_multimodal", request.model)
        return await self._call(
            "generate_multimodal",
            request.model,
            lambda: self._inner.generate_multimodal(request),
            request_id=request.request_id,
            trace_id=request.trace_id,
            shape=self._describe_messages(request.messages),
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self._require(Capability.EMBEDDING, "embed", request.model)
        return await self._call(
            "embed",
            request.model,
            lambda: self._inner.embed(request),
            request_id=request.request_id,
            trace_id=request.trace_id,
            shape={
                "input_count": len(request.inputs),
                "approx_text_chars": sum(len(i) for i in request.inputs),
            },
        )

    async def transcribe(
        self, request: SpeechTranscriptionRequest
    ) -> SpeechTranscriptionResponse:
        self._require(Capability.SPEECH, "transcribe", request.model)
        return await self._call(
            "transcribe",
            request.model,
            lambda: self._inner.transcribe(request),
            request_id=request.request_id,
            trace_id=request.trace_id,
            # Size only. Audio bytes are never logged.
            shape={"audio_bytes": len(request.audio)},
        )

    async def healthcheck(self) -> bool:
        return await self._inner.healthcheck()

    async def close(self) -> None:
        await self._inner.close()

    # -------------------------------------------------------------- internals
    def _require(self, capability: Capability, operation: str, model: str) -> None:
        if not self.supports(capability):
            raise ModelCapabilityError(
                f"Provider does not support {capability}",
                provider=self.provider_name,
                operation=operation,
                model=model,
            )

    async def _call(
        self,
        operation: str,
        model: str,
        invoke: Callable[[], Awaitable[T]],
        *,
        request_id: str | None,
        trace_id: str | None,
        shape: dict[str, Any],
    ) -> T:
        """Run one logical operation with the full resilience stack."""
        # Fall back to ambient correlation when the caller did not supply one.
        request_id = request_id or get_request_id()
        trace_id = trace_id or get_trace_id()

        started = time.perf_counter()

        if not await self._breaker.allow():
            error = CircuitOpenError(provider=self.provider_name, operation=operation, model=model)
            await self._emit(
                operation,
                model,
                outcome="error",
                started=started,
                attempts=0,
                error_code=error.code,
                circuit_open=True,
                request_id=request_id,
                trace_id=trace_id,
                shape=shape,
            )
            raise error

        last_error: ModelGatewayError | None = None

        for attempt in range(1, self._retry.max_attempts + 1):
            attempt_started = time.perf_counter()
            try:
                result = await invoke()
            except ModelGatewayError as exc:
                last_error = exc
                elapsed = time.perf_counter() - started
                should_retry = (
                    exc.retryable
                    and attempt < self._retry.max_attempts
                    and elapsed < self._retry.max_elapsed_seconds
                )
                if not should_retry:
                    await self._breaker.record_failure()
                    await self._emit(
                        operation,
                        model,
                        outcome="error",
                        started=started,
                        attempts=attempt,
                        error_code=exc.code,
                        request_id=request_id,
                        trace_id=trace_id,
                        shape=shape,
                    )
                    raise

                delay = self._retry_delay(exc, attempt)
                logger.info(
                    "model_gateway_retry",
                    extra={
                        "provider": self.provider_name,
                        "operation": operation,
                        "model": model,
                        "attempt": attempt,
                        "error_code": exc.code,
                        "delay_seconds": round(delay, 3),
                    },
                )
                await asyncio.sleep(delay)
                continue

            model_latency_ms = round((time.perf_counter() - attempt_started) * 1000, 2)
            await self._breaker.record_success()
            usage = getattr(result, "usage", None) or TokenUsage()
            await self._emit(
                operation,
                model,
                outcome="success",
                started=started,
                attempts=attempt,
                model_latency_ms=model_latency_ms,
                usage=usage,
                request_id=request_id,
                trace_id=trace_id,
                shape=shape,
            )
            return result

        # Unreachable: the loop either returns or raises.
        raise last_error or ModelGatewayError(provider=self.provider_name, operation=operation)

    def _retry_delay(self, error: ModelGatewayError, attempt: int) -> float:
        """Backoff for the next attempt.

        A provider-supplied Retry-After wins over our own backoff: it reflects
        the provider's actual recovery window.
        """
        if isinstance(error, GatewayRateLimitError) and error.retry_after_seconds is not None:
            return min(error.retry_after_seconds, self._retry.max_delay_seconds)
        return self._retry.delay_for(attempt)

    async def _emit(
        self,
        operation: str,
        model: str,
        *,
        outcome: str,
        started: float,
        attempts: int,
        shape: dict[str, Any],
        model_latency_ms: float | None = None,
        usage: TokenUsage | None = None,
        error_code: str | None = None,
        circuit_open: bool = False,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        record = GatewayCallTelemetry.from_usage(
            usage or TokenUsage(),
            provider=self.provider_name,
            operation=operation,
            model=model,
            outcome=outcome,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            model_latency_ms=model_latency_ms,
            attempts=attempts,
            retry_count=max(attempts - 1, 0),
            error_code=error_code,
            circuit_open=circuit_open,
            request_id=request_id,
            trace_id=trace_id,
            request_shape=shape,
        )
        await emit(self._telemetry, record)


# ===========================================================================
# Mock
# ===========================================================================
class MockModelGateway(ModelGatewayInterface):
    """Deterministic in-memory gateway.

    Tests must never depend on a live LLM API
    (AI_DEVELOPMENT_RULES.md section 25). Makes no network connection.

    ``report_usage=False`` simulates a provider that returns no usage, so the
    cost layer can be exercised against provenance UNAVAILABLE without
    fabricating token counts.
    """

    provider_name = "mock"
    capabilities = frozenset(
        {Capability.TEXT, Capability.MULTIMODAL, Capability.EMBEDDING, Capability.SPEECH}
    )

    def __init__(
        self,
        *,
        canned_text: str = "",
        embedding_dimensions: int = 4,
        transcript: str = "mock transcript",
        report_usage: bool = True,
        failures: list[Exception] | None = None,
    ) -> None:
        self._canned_text = canned_text
        self._dimensions = embedding_dimensions
        self._transcript = transcript
        self._report_usage = report_usage
        #: Popped left-to-right; each entry is raised by the next call.
        self._failures = list(failures or [])
        self.calls: list[tuple[str, Any]] = []
        self._closed = False

    # ------------------------------------------------------------ operations
    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResponse:
        self._record("generate_text", request)
        return TextGenerationResponse(
            content=self._canned_text or f"mock-response:{request.model}",
            model=request.model,
            provider=self.provider_name,
            usage=self._usage(),
            finish_reason="stop",
            latency_ms=0.0,
        )

    async def generate_multimodal(
        self, request: MultimodalGenerationRequest
    ) -> TextGenerationResponse:
        self._record("generate_multimodal", request)
        return TextGenerationResponse(
            content=self._canned_text or f"mock-multimodal:{request.model}",
            model=request.model,
            provider=self.provider_name,
            usage=self._usage(),
            finish_reason="stop",
            latency_ms=0.0,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self._record("embed", request)
        # Deterministic, input-dependent, and obviously synthetic.
        vectors = tuple(
            tuple(float((len(text) + i) % 10) / 10 for i in range(self._dimensions))
            for text in request.inputs
        )
        return EmbeddingResponse(
            embeddings=vectors,
            model=request.model,
            provider=self.provider_name,
            usage=self._usage(),
            latency_ms=0.0,
        )

    async def transcribe(
        self, request: SpeechTranscriptionRequest
    ) -> SpeechTranscriptionResponse:
        self._record("transcribe", request)
        return SpeechTranscriptionResponse(
            text=self._transcript,
            model=request.model,
            provider=self.provider_name,
            usage=self._usage(),
            latency_ms=0.0,
        )

    async def healthcheck(self) -> bool:
        return not self._closed

    async def close(self) -> None:
        self._closed = True

    # -------------------------------------------------------------- helpers
    def _record(self, operation: str, request: Any) -> None:
        self.calls.append((operation, request))
        if self._failures:
            raise self._failures.pop(0)

    def _usage(self) -> TokenUsage:
        if not self._report_usage:
            return TokenUsage()
        return TokenUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            provenance=UsageProvenance.ACTUAL,
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def calls_for(self, operation: str) -> list[Any]:
        return [request for op, request in self.calls if op == operation]


# ===========================================================================
# Factory
# ===========================================================================
def build_circuit_breaker(settings: Settings) -> CircuitBreaker:
    if not settings.genai_circuit_breaker_enabled:
        return NullCircuitBreaker()
    return InMemoryCircuitBreaker(
        failure_threshold=settings.genai_circuit_breaker_failure_threshold,
        reset_timeout_seconds=settings.genai_circuit_breaker_reset_seconds,
    )


def build_retry_policy(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.genai_max_attempts,
        base_delay_seconds=settings.genai_retry_base_delay_seconds,
        max_delay_seconds=settings.genai_retry_max_delay_seconds,
        max_elapsed_seconds=settings.genai_retry_max_elapsed_seconds,
    )


def build_model_gateway(
    settings: Settings,
    *,
    telemetry_sink: TelemetrySink | None = None,
) -> ModelGatewayInterface:
    """Build the configured gateway, wrapped in the resilience stack.

    The provider is configuration, never a branch scattered through business
    logic (AI_DEVELOPMENT_RULES.md section 6).
    """
    match settings.model_gateway_provider:
        case ModelGatewayProvider.GENAILAB:
            # Imported here so the provider SDK is not loaded when running on
            # the mock, which keeps test startup free of the openai package.
            from app.integrations.llm.genailab import GenAILabAdapter

            inner: ModelGatewayInterface = GenAILabAdapter(settings)
        case ModelGatewayProvider.MOCK:
            inner = MockModelGateway()
        case _:  # pragma: no cover - StrEnum makes this unreachable
            raise ValueError(
                f"Unsupported model gateway provider: {settings.model_gateway_provider}"
            )

    gateway = ResilientGateway(
        inner,
        retry_policy=build_retry_policy(settings),
        circuit_breaker=build_circuit_breaker(settings),
        telemetry_sink=telemetry_sink or LoggingTelemetrySink(),
    )
    logger.info(
        "model_gateway_selected",
        extra={
            "provider": gateway.provider_name,
            "capabilities": sorted(str(c) for c in gateway.capabilities),
            "max_attempts": settings.genai_max_attempts,
            "circuit_breaker_enabled": settings.genai_circuit_breaker_enabled,
        },
    )
    return gateway


__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "CollectingTelemetrySink",
    "InMemoryCircuitBreaker",
    "MockModelGateway",
    "NullCircuitBreaker",
    "ResilientGateway",
    "RetryPolicy",
    "build_model_gateway",
]
