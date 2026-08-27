"""Execution limits.

SECURITY.md section 19 requires bounded retries, a maximum tool-call count, a
maximum execution duration and explicit termination conditions, and states
plainly: never allow an agent loop to continue indefinitely.

The budget is held by an ``ExecutionBudget`` that is *consumed* as work happens.
Checking a limit and then not decrementing it is how a loop that "checks its
limit" still runs forever, so every check here mutates.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.guardrails.errors import (
    IterationLimitExceeded,
    TokenLimitExceeded,
    ToolCallLimitExceeded,
)

logger = get_logger(__name__)

#: Conservative defaults. A workflow that needs more must say so explicitly
#: rather than inheriting an unbounded allowance.
DEFAULT_MAX_ITERATIONS = 25
DEFAULT_MAX_TOOL_CALLS = 10
DEFAULT_MAX_DURATION_SECONDS = 300.0


@dataclass
class ExecutionBudget:
    """A consumable allowance for one execution.

    Not frozen: it is spent. ``remaining_*`` falls as work happens, which is
    what makes the limit real rather than advisory.
    """

    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    max_total_tokens: int | None = None
    max_duration_seconds: float = DEFAULT_MAX_DURATION_SECONDS

    iterations_used: int = 0
    tool_calls_used: int = 0
    tokens_used: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls cannot be negative")
        if self.max_total_tokens is not None and self.max_total_tokens < 1:
            raise ValueError("max_total_tokens must be positive when set")

    # ------------------------------------------------------------ queries
    @property
    def iterations_remaining(self) -> int:
        return max(self.max_iterations - self.iterations_used, 0)

    @property
    def tool_calls_remaining(self) -> int:
        return max(self.max_tool_calls - self.tool_calls_used, 0)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def is_expired(self) -> bool:
        return self.elapsed_seconds >= self.max_duration_seconds

    # ------------------------------------------------------------- spend
    def consume_iteration(self) -> None:
        """Record one workflow step.

        Called at the top of every loop body. A graph that forgets to call this
        is unbounded regardless of what it declares, which is why the wall-clock
        deadline below is checked here too — it bounds the run even when the
        step counter is mismanaged.
        """
        if self.is_expired:
            logger.warning(
                "execution_deadline_exceeded",
                extra={
                    "elapsed_seconds": round(self.elapsed_seconds, 2),
                    "max_duration_seconds": self.max_duration_seconds,
                },
            )
            raise IterationLimitExceeded(
                "Workflow exceeded its maximum duration",
                reason="duration_exceeded",
                details={"max_duration_seconds": self.max_duration_seconds},
            )

        if self.iterations_used >= self.max_iterations:
            logger.warning(
                "iteration_limit_exceeded",
                extra={"max_iterations": self.max_iterations},
            )
            raise IterationLimitExceeded(
                reason="max_iterations",
                details={"max_iterations": self.max_iterations},
            )
        self.iterations_used += 1

    def consume_tool_call(self, count: int = 1) -> None:
        """Record tool calls, refusing the ones that would exceed the ceiling."""
        if count < 0:
            raise ValueError("count cannot be negative")
        if self.tool_calls_used + count > self.max_tool_calls:
            logger.warning(
                "tool_call_limit_exceeded",
                extra={
                    "max_tool_calls": self.max_tool_calls,
                    "used": self.tool_calls_used,
                    "requested": count,
                },
            )
            raise ToolCallLimitExceeded(
                reason="max_tool_calls",
                details={
                    "max_tool_calls": self.max_tool_calls,
                    "tool_calls_used": self.tool_calls_used,
                },
            )
        self.tool_calls_used += count

    def consume_tokens(self, count: int) -> None:
        """Record token spend against the ceiling, when one is configured.

        ``max_total_tokens`` is optional because a workload may legitimately
        have no token ceiling beyond its budget. It is never defaulted to a
        guess.
        """
        if count < 0:
            raise ValueError("count cannot be negative")
        self.tokens_used += count
        if self.max_total_tokens is not None and self.tokens_used > self.max_total_tokens:
            logger.warning(
                "token_limit_exceeded",
                extra={
                    "max_total_tokens": self.max_total_tokens,
                    "tokens_used": self.tokens_used,
                },
            )
            raise TokenLimitExceeded(
                reason="max_total_tokens",
                details={"max_total_tokens": self.max_total_tokens},
            )


def enforce_token_limit(requested_tokens: int, ceiling: int | None) -> None:
    """Pre-flight check before a request is sent.

    Separate from ``ExecutionBudget.consume_tokens`` because this one runs
    *before* spending anything: a request that cannot possibly fit should be
    refused rather than sent and truncated.
    """
    if ceiling is None:
        return
    if requested_tokens > ceiling:
        raise TokenLimitExceeded(
            reason="request_exceeds_ceiling",
            details={"max_total_tokens": ceiling, "requested_tokens": requested_tokens},
        )
