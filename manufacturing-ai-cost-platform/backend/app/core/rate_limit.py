"""Rate limiting hook (SECURITY.md sections 18 and 20).

An abstraction plus a per-process implementation. The abstraction is the point:
per-process counters do not hold across replicas, so a deployment running more
than one worker needs a shared backend. Making that a seam rather than an
assumption is what lets Redis slot in without touching call sites.

Deliberately a *hook*: the limiter decides, the caller enforces. That keeps the
policy (who is limited, how hard) out of the transport and testable on its own.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from app.core.logging import get_logger
from app.security.events import SecurityEvent, record_security_event

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """How many requests a key may make in a window."""

    max_requests: int
    window_seconds: float

    def __post_init__(self) -> None:
        if self.max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: float | None = None


class RateLimiter(ABC):
    """The hook the application depends on."""

    @abstractmethod
    async def check(self, key: str, policy: RateLimitPolicy) -> RateLimitDecision:
        """Record an attempt for ``key`` and say whether it may proceed."""


class NullRateLimiter(RateLimiter):
    """Allows everything. For tests that are not about rate limiting."""

    async def check(self, key: str, policy: RateLimitPolicy) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=policy.max_requests)


class InMemoryRateLimiter(RateLimiter):
    """Sliding-window counter held in this process.

    Correct for a single worker and useful in development. It is **not** a
    shared limit: with N replicas the effective ceiling is N times the policy.
    A deployment that needs a real global limit implements ``RateLimiter``
    against Redis — which is the abstraction's whole purpose.
    """

    def __init__(self, time_source: Callable[[], float] = time.monotonic) -> None:
        self._now = time_source
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def check(self, key: str, policy: RateLimitPolicy) -> RateLimitDecision:
        now = self._now()
        cutoff = now - policy.window_seconds
        hits = self._hits[key]

        # Drop everything outside the window before counting.
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= policy.max_requests:
            retry_after = max(hits[0] + policy.window_seconds - now, 0.0)
            record_security_event(
                SecurityEvent.RATE_LIMIT_EXCEEDED,
                reason="window_exhausted",
                max_requests=policy.max_requests,
                window_seconds=policy.window_seconds,
            )
            return RateLimitDecision(
                allowed=False, remaining=0, retry_after_seconds=retry_after
            )

        hits.append(now)
        return RateLimitDecision(
            allowed=True, remaining=policy.max_requests - len(hits)
        )

    def reset(self, key: str | None = None) -> None:
        """Clear counters. For tests and for an operator clearing a bad state."""
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)


def rate_limit_key(*, tenant_id: str | None, subject: str | None, route: str) -> str:
    """Build a limiter key.

    Keyed on the authenticated principal rather than the client address:
    limiting by IP punishes everyone behind a shared egress, and an
    unauthenticated caller has already been refused by the time this matters.
    """
    return f"{tenant_id or 'anonymous'}:{subject or 'anonymous'}:{route}"
