"""Resilience tests: timeout, bounded retry, backoff, circuit breaker.

Implements the checks behind SECURITY.md section 19. No live provider call is
made; failures are injected into the mock gateway.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.llm.client import (
    CircuitState,
    InMemoryCircuitBreaker,
    MockModelGateway,
    NullCircuitBreaker,
    ResilientGateway,
    RetryPolicy,
)
from app.integrations.llm.errors import (
    CircuitOpenError,
    GatewayAuthenticationError,
    GatewayBadRequestError,
    GatewayRateLimitError,
    GatewayTimeoutError,
    GatewayUnavailableError,
    ModelGatewayError,
)
from app.integrations.llm.interface import Message, Role, TextGenerationRequest
from app.integrations.llm.telemetry import CollectingTelemetrySink

MODEL = "registry-supplied-model-id"

# Retries are real awaits; zero delay keeps the suite fast without disabling
# the retry logic itself.
NO_DELAY = RetryPolicy(max_attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0)


def _request() -> TextGenerationRequest:
    return TextGenerationRequest(
        model=MODEL, messages=(Message(role=Role.USER, content="hello"),)
    )


def _gateway(
    failures: list[Exception],
    *,
    policy: RetryPolicy | None = None,
    breaker: Any = None,
    sink: CollectingTelemetrySink | None = None,
) -> tuple[ResilientGateway, MockModelGateway, CollectingTelemetrySink]:
    inner = MockModelGateway(failures=failures)
    telemetry = sink or CollectingTelemetrySink()
    gateway = ResilientGateway(
        inner,
        retry_policy=policy or NO_DELAY,
        circuit_breaker=breaker or NullCircuitBreaker(),
        telemetry_sink=telemetry,
    )
    return gateway, inner, telemetry


# ===========================================================================
# Retry classification
# ===========================================================================
@pytest.mark.parametrize(
    "error",
    [
        GatewayTimeoutError(),
        GatewayUnavailableError(),
        GatewayRateLimitError(),
    ],
)
async def test_retryable_errors_are_retried_and_can_succeed(
    error: ModelGatewayError,
) -> None:
    gateway, inner, telemetry = _gateway([error])

    response = await gateway.generate_text(_request())

    assert response.content
    assert inner.call_count == 2  # one failure, one success
    record = telemetry.last
    assert record is not None
    assert record.attempts == 2
    assert record.retry_count == 1


@pytest.mark.parametrize(
    "error",
    [
        GatewayAuthenticationError(),
        GatewayBadRequestError(),
    ],
)
async def test_non_retryable_errors_are_raised_immediately(
    error: ModelGatewayError,
) -> None:
    """Retrying a bad key or a malformed request burns budget and never works."""
    gateway, inner, telemetry = _gateway([error, error, error])

    with pytest.raises(type(error)):
        await gateway.generate_text(_request())

    assert inner.call_count == 1
    record = telemetry.last
    assert record is not None
    assert record.outcome == "error"
    assert record.attempts == 1
    assert record.error_code == error.code


async def test_retries_are_bounded() -> None:
    """A permanently failing provider must not be retried forever."""
    failures = [GatewayUnavailableError() for _ in range(10)]
    gateway, inner, telemetry = _gateway(failures, policy=NO_DELAY)

    with pytest.raises(GatewayUnavailableError):
        await gateway.generate_text(_request())

    assert inner.call_count == NO_DELAY.max_attempts == 3
    record = telemetry.last
    assert record is not None
    assert record.attempts == 3
    assert record.retry_count == 2


async def test_max_attempts_of_one_disables_retrying() -> None:
    policy = RetryPolicy(max_attempts=1, base_delay_seconds=0.0)
    gateway, inner, _ = _gateway([GatewayTimeoutError()], policy=policy)

    with pytest.raises(GatewayTimeoutError):
        await gateway.generate_text(_request())

    assert inner.call_count == 1


def test_retry_policy_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RetryPolicy(max_attempts=0)


# ===========================================================================
# Backoff
# ===========================================================================
def test_backoff_grows_exponentially_and_is_capped() -> None:
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=8.0)

    # Full jitter: each delay is in [0, ceiling]. Sampled repeatedly so the
    # assertion is about the bound, not one lucky draw.
    for attempt, ceiling in [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (9, 8.0)]:
        samples = [policy.delay_for(attempt) for _ in range(200)]
        assert all(0.0 <= s <= ceiling for s in samples), attempt
        assert max(samples) > ceiling * 0.5, attempt  # jitter spans the range


def test_backoff_never_exceeds_the_configured_maximum() -> None:
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=3.0)
    assert all(policy.delay_for(n) <= 3.0 for n in range(1, 20))


async def test_provider_retry_after_takes_precedence() -> None:
    """A provider's own recovery window beats our guess."""
    policy = RetryPolicy(max_attempts=2, base_delay_seconds=0.0, max_delay_seconds=30.0)
    gateway, _, _ = _gateway([], policy=policy)

    delay = gateway._retry_delay(  # noqa: SLF001
        GatewayRateLimitError(retry_after_seconds=7.0), attempt=1
    )
    assert delay == 7.0


async def test_retry_after_is_still_capped() -> None:
    policy = RetryPolicy(max_attempts=2, base_delay_seconds=0.0, max_delay_seconds=5.0)
    gateway, _, _ = _gateway([], policy=policy)

    delay = gateway._retry_delay(  # noqa: SLF001
        GatewayRateLimitError(retry_after_seconds=3600.0), attempt=1
    )
    assert delay == 5.0


# ===========================================================================
# Circuit breaker
# ===========================================================================
async def test_breaker_opens_after_the_failure_threshold() -> None:
    breaker = InMemoryCircuitBreaker(failure_threshold=2, reset_timeout_seconds=60.0)
    policy = RetryPolicy(max_attempts=1, base_delay_seconds=0.0)

    for _ in range(2):
        gateway, _, _ = _gateway(
            [GatewayUnavailableError()], policy=policy, breaker=breaker
        )
        with pytest.raises(GatewayUnavailableError):
            await gateway.generate_text(_request())

    assert breaker.state is CircuitState.OPEN


async def test_open_breaker_fails_fast_without_calling_the_provider() -> None:
    breaker = InMemoryCircuitBreaker(failure_threshold=1, reset_timeout_seconds=60.0)
    policy = RetryPolicy(max_attempts=1, base_delay_seconds=0.0)

    gateway, inner, _ = _gateway([GatewayUnavailableError()], policy=policy, breaker=breaker)
    with pytest.raises(GatewayUnavailableError):
        await gateway.generate_text(_request())

    calls_before = inner.call_count
    gateway2, inner2, telemetry = _gateway([], policy=policy, breaker=breaker)

    with pytest.raises(CircuitOpenError):
        await gateway2.generate_text(_request())

    # The provider was not touched at all.
    assert inner2.call_count == 0
    assert calls_before == 1
    record = telemetry.last
    assert record is not None
    assert record.circuit_open is True
    assert record.attempts == 0


async def test_breaker_half_opens_after_the_reset_timeout() -> None:
    clock = {"now": 0.0}
    breaker = InMemoryCircuitBreaker(
        failure_threshold=1,
        reset_timeout_seconds=30.0,
        time_source=lambda: clock["now"],
    )
    policy = RetryPolicy(max_attempts=1, base_delay_seconds=0.0)

    gateway, _, _ = _gateway([GatewayUnavailableError()], policy=policy, breaker=breaker)
    with pytest.raises(GatewayUnavailableError):
        await gateway.generate_text(_request())
    assert breaker.state is CircuitState.OPEN

    # Still inside the window.
    clock["now"] = 29.0
    assert await breaker.allow() is False

    # Window elapsed: one probe is admitted.
    clock["now"] = 31.0
    assert await breaker.allow() is True
    assert breaker.state is CircuitState.HALF_OPEN


async def test_successful_probe_closes_the_breaker() -> None:
    clock = {"now": 0.0}
    breaker = InMemoryCircuitBreaker(
        failure_threshold=1, reset_timeout_seconds=10.0, time_source=lambda: clock["now"]
    )
    policy = RetryPolicy(max_attempts=1, base_delay_seconds=0.0)

    gateway, _, _ = _gateway([GatewayUnavailableError()], policy=policy, breaker=breaker)
    with pytest.raises(GatewayUnavailableError):
        await gateway.generate_text(_request())

    clock["now"] = 20.0
    healthy, inner, _ = _gateway([], policy=policy, breaker=breaker)
    response = await healthy.generate_text(_request())

    assert response.content
    assert breaker.state is CircuitState.CLOSED
    assert inner.call_count == 1


async def test_failed_probe_reopens_the_breaker() -> None:
    clock = {"now": 0.0}
    breaker = InMemoryCircuitBreaker(
        failure_threshold=1, reset_timeout_seconds=10.0, time_source=lambda: clock["now"]
    )
    policy = RetryPolicy(max_attempts=1, base_delay_seconds=0.0)

    gateway, _, _ = _gateway([GatewayUnavailableError()], policy=policy, breaker=breaker)
    with pytest.raises(GatewayUnavailableError):
        await gateway.generate_text(_request())

    clock["now"] = 20.0
    probe, _, _ = _gateway([GatewayUnavailableError()], policy=policy, breaker=breaker)
    with pytest.raises(GatewayUnavailableError):
        await probe.generate_text(_request())

    assert breaker.state is CircuitState.OPEN
    # The reset clock restarted, so the old window no longer applies.
    clock["now"] = 25.0
    assert await breaker.allow() is False


async def test_success_resets_the_failure_count() -> None:
    """Consecutive failures, not lifetime failures, open the circuit."""
    breaker = InMemoryCircuitBreaker(failure_threshold=3, reset_timeout_seconds=60.0)
    policy = RetryPolicy(max_attempts=1, base_delay_seconds=0.0)

    for _ in range(2):
        gateway, _, _ = _gateway(
            [GatewayUnavailableError()], policy=policy, breaker=breaker
        )
        with pytest.raises(GatewayUnavailableError):
            await gateway.generate_text(_request())

    healthy, _, _ = _gateway([], policy=policy, breaker=breaker)
    await healthy.generate_text(_request())

    for _ in range(2):
        gateway, _, _ = _gateway(
            [GatewayUnavailableError()], policy=policy, breaker=breaker
        )
        with pytest.raises(GatewayUnavailableError):
            await gateway.generate_text(_request())

    assert breaker.state is CircuitState.CLOSED


def test_breaker_rejects_a_zero_threshold() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        InMemoryCircuitBreaker(failure_threshold=0)


async def test_null_breaker_always_allows() -> None:
    breaker = NullCircuitBreaker()
    for _ in range(100):
        await breaker.record_failure()
    assert await breaker.allow() is True
    assert breaker.state is CircuitState.CLOSED


# ===========================================================================
# Correlation
# ===========================================================================
async def test_ambient_request_id_is_used_when_none_is_supplied() -> None:
    """Telemetry stays correlated even when the caller omits the ids."""
    from app.core.context import bind_correlation, reset_correlation

    sink = CollectingTelemetrySink()
    gateway, _, _ = _gateway([], sink=sink)

    tokens = bind_correlation(request_id="ambient-req", trace_id="ambient-trace")
    try:
        await gateway.generate_text(_request())
    finally:
        reset_correlation(tokens)

    record = sink.last
    assert record is not None
    assert record.request_id == "ambient-req"
    assert record.trace_id == "ambient-trace"
