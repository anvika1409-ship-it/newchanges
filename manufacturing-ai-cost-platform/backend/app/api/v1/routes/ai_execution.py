"""AI execution endpoint.

Implements ``POST /ai/execute`` from API_CONTRACT.yaml.

This route delegates to ``AIExecutionService``, which coordinates the
workflow lifecycle.  The route itself handles only request parsing,
response formatting, and dependency wiring.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from app.core.errors import BadRequestError
from app.core.logging import get_logger
from app.services.execution import AIExecutionService
from app.telemetry.emitter import TelemetryEmitter
from app.telemetry.tracker import CostTracker

try:
    from app.security.dependencies import get_current_principal
    _AUTH_DEPS = [Depends(get_current_principal)]
except ImportError:
    _AUTH_DEPS = []

logger = get_logger(__name__)

router = APIRouter(tags=["AI Execution"])


# ── Request / Response models (API_CONTRACT.yaml) ─────────────────


class AIExecutionRequest(BaseModel):
    """Maps to ``AIExecutionRequest`` in API_CONTRACT.yaml."""

    workload_type: str = Field(
        min_length=1,
        description="Workflow type: predictive_maintenance, quality_check, supply_chain",
    )
    request_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Workflow-specific input data",
    )
    business_priority: Literal["LOW", "NORMAL", "HIGH", "CRITICAL"] = "NORMAL"


class UsageResponse(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class CostResponse(BaseModel):
    amount: float = 0.0
    currency: str = "USD"
    provenance: Literal["ACTUAL", "ESTIMATED", "UNAVAILABLE"] = "ESTIMATED"


class AIExecutionResponse(BaseModel):
    """Maps to ``AIExecutionResponse`` in API_CONTRACT.yaml."""

    request_id: str
    status: str
    result: dict[str, Any] | None = None
    usage: UsageResponse = Field(default_factory=UsageResponse)
    cost: CostResponse = Field(default_factory=CostResponse)
    error: str | None = None


# ── Endpoint ──────────────────────────────────────────────────────


@router.post(
    "/ai/execute",
    summary="Execute an AI workload",
    response_model=AIExecutionResponse,
    status_code=status.HTTP_200_OK,
    dependencies=_AUTH_DEPS,
)
async def execute_ai_workload(
    body: AIExecutionRequest,
    request: Request,
) -> AIExecutionResponse:
    """Execute an AI workflow.

    Delegates to ``AIExecutionService`` which looks up the workflow, validates
    input, runs the pipeline, and tracks telemetry and cost.
    """
    # Wire dependencies from app state.
    gateway = request.app.state.model_gateway
    telemetry = TelemetryEmitter()
    cost_tracker = CostTracker()

    service = AIExecutionService(
        model_gateway=gateway,
        telemetry=telemetry,
        cost_tracker=cost_tracker,
    )

    try:
        result = await service.execute(
            workflow_type=body.workload_type,
            input_data=body.request_payload,
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc

    # Map internal result to API response.
    if result["status"] == "failed":
        return AIExecutionResponse(
            request_id=result["execution_id"],
            status="failed",
            error=result.get("error"),
        )

    return AIExecutionResponse(
        request_id=result["execution_id"],
        status=result["status"],
        result=result.get("output"),
        usage=UsageResponse(
            input_tokens=result.get("prompt_tokens"),
            output_tokens=result.get("completion_tokens"),
            total_tokens=result.get("total_tokens"),
        ),
        cost=CostResponse(
            amount=result.get("cost_usd", 0.0),
            currency="USD",
            provenance="ESTIMATED",
        ),
    )
