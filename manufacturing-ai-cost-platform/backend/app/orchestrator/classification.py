"""Deterministic request classification.

Complexity and risk are decided by rules over request shape, never by asking a
model. ARCHITECTURE.md section 4 is explicit: the optimization layer "should not
invoke an expensive LLM on every request merely to decide a runtime model", and
AI_WORKFLOWS.md section 2 repeats it — "The platform must not call an expensive
LLM to choose the model for every image."

Paying for a model call to decide which model to call would invert the product:
the cost-control layer would become the largest cost. Every rule here is
arithmetic over data already in the request.

The thresholds are configurable rather than hard-coded judgements, because the
right cut-off is workload-specific and should be tuned from telemetry rather
than guessed once.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Complexity(StrEnum):
    """Matches ``ExecutionPlan.complexity`` in API_CONTRACT.yaml."""

    SIMPLE = "SIMPLE"
    MEDIUM = "MEDIUM"
    COMPLEX = "COMPLEX"


class BusinessPriority(StrEnum):
    """Matches ``AIExecutionRequest.business_priority``."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskLevel(StrEnum):
    """Matches ``ExecutionPlan.risk_level``."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class ComplexityThresholds:
    """Cut-offs for the complexity classifier.

    Configurable per deployment. These defaults are starting points to be tuned
    from measured telemetry, not claims about what any model needs.
    """

    #: Approximate payload characters above which a request stops being SIMPLE.
    medium_chars: int = 2_000
    #: ...and above which it becomes COMPLEX.
    complex_chars: int = 12_000

    #: More than one image implies comparison or multi-view inspection.
    complex_image_count: int = 2

    #: A caller demanding this quality or better needs a stronger model.
    high_quality_requirement: float = 0.9


DEFAULT_THRESHOLDS = ComplexityThresholds()


@dataclass(frozen=True, slots=True)
class ClassificationInput:
    """Everything the classifier is allowed to look at."""

    workload_type: str
    business_priority: BusinessPriority
    payload: dict[str, Any]
    image_count: int = 0
    quality_requirement: float | None = None
    #: Declared workload risk, read from the ``workloads`` record when known.
    workload_risk_level: str | None = None


def _payload_size(payload: dict[str, Any]) -> int:
    """Approximate character size of the request payload.

    A cheap structural measure, not a token count. Token counting needs the
    target model's tokenizer, and the model has not been chosen yet — that is
    the decision this classification feeds.
    """
    total = 0
    stack: list[Any] = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            total += len(item)
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif item is not None:
            total += len(str(item))
    return total


def classify_complexity(
    request: ClassificationInput,
    thresholds: ComplexityThresholds = DEFAULT_THRESHOLDS,
) -> Complexity:
    """Classify a request as SIMPLE, MEDIUM or COMPLEX.

    Pure and deterministic: the same request always classifies the same way, so
    a routing decision can be explained and replayed from the telemetry record.
    """
    size = _payload_size(request.payload)

    # Any multi-image request is comparison work regardless of text size.
    if request.image_count >= thresholds.complex_image_count:
        return Complexity.COMPLEX

    if size >= thresholds.complex_chars:
        return Complexity.COMPLEX

    # An explicit high quality bar is the caller telling us this is hard.
    if (
        request.quality_requirement is not None
        and request.quality_requirement >= thresholds.high_quality_requirement
    ):
        return Complexity.COMPLEX

    if size >= thresholds.medium_chars or request.image_count == 1:
        return Complexity.MEDIUM

    return Complexity.SIMPLE


def determine_risk(request: ClassificationInput) -> RiskLevel:
    """Determine the risk level for a request.

    Risk is taken from the workload record when it declares one — the workloads
    table carries ``risk_level`` (DATABASE_SCHEMA.md section 9) and an operator's
    classification of a workload outranks anything inferred per request.

    Otherwise it is derived from business priority. A CRITICAL-priority request
    is by definition one where being wrong matters, which is what makes it a
    candidate for human approval (SECURITY.md section 14).
    """
    declared = request.workload_risk_level
    if declared:
        try:
            return RiskLevel(declared.strip().upper())
        except ValueError:
            # An unrecognised stored value is not silently treated as LOW.
            # Falling through to the priority mapping keeps it conservative.
            pass

    return {
        BusinessPriority.LOW: RiskLevel.LOW,
        BusinessPriority.NORMAL: RiskLevel.LOW,
        BusinessPriority.HIGH: RiskLevel.MEDIUM,
        BusinessPriority.CRITICAL: RiskLevel.HIGH,
    }[request.business_priority]
