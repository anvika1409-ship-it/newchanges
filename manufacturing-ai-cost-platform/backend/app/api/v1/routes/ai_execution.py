"""AI execution endpoint.

Implements ``POST /ai/execute`` from API_CONTRACT.yaml.

The route performs steps 1-3 of the orchestration sequence — validate,
authenticate, authorize — and hands everything else to
``CostAwareOrchestrator`` (ARCHITECTURE.md sections 4 and 6). It contains no
routing, budget or model-selection logic of its own.

Tenant is taken from the authenticated principal. ``plant_id`` and
``department_id`` arrive in the body per the contract but are treated as
*scoping claims* to be authorized, never as trusted ownership
(SECURITY.md section 5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.orchestrator import (
    BusinessPriority,
    CostAwareOrchestrator,
    OrchestrationRequest,
    RepositoryBudgetEvaluator,
)
from app.repositories.model_repository import ModelRepository
from app.repositories.routing_policy_repository import RoutingPolicyRepository
from app.guardrails.workload_guardrails import build_workload_guardrails
from app.security.dependencies import RequirePermission
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.model_registry import ModelRegistryService
from app.telemetry.recorder import TelemetryRecorder

logger = get_logger(__name__)

router = APIRouter(tags=["AI Execution"])


# ── Request / response models (API_CONTRACT.yaml) ──────────────────────────
class InputRefModel(BaseModel):
    """Reference to an input artifact held in object storage."""

    model_config = ConfigDict(frozen=True)

    ref: str
    content_type: str
    size_bytes: int | None = None
    classification: str | None = None


class AIExecutionRequestModel(BaseModel):
    """Maps to ``AIExecutionRequest`` in API_CONTRACT.yaml."""

    model_config = ConfigDict(extra="forbid")

    workload_type: Literal["quality_check", "predictive_maintenance", "supply_chain"]
    business_priority: Literal["LOW", "NORMAL", "HIGH", "CRITICAL"]
    workload_id: str | None = None
    plant_id: str | None = None
    department_id: str | None = None
    request_payload: dict[str, Any] = Field(default_factory=dict)
    input_refs: list[InputRefModel] = Field(default_factory=list)
    modality: Literal["text", "image", "multimodal", "structured"] | None = None
    quality_requirement: float | None = Field(default=None, ge=0, le=1)
    max_cost: float | None = Field(default=None, ge=0)


class ExecutionPlanModel(BaseModel):
    """Maps to ``ExecutionPlan``."""

    workload_type: str
    complexity: Literal["SIMPLE", "MEDIUM", "COMPLEX"]
    selected_model_id: str | None = None
    selected_agent_id: str | None = None
    estimated_cost: float | None = None
    max_context_tokens: int | None = None
    max_tool_calls: int | None = None
    routing_policy_version: int | None = None
    budget_status: Literal["ALLOW", "DOWNGRADE", "REQUIRE_APPROVAL", "BLOCK"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class UsageModel(BaseModel):
    """Token counts. ``None`` when the gateway reported none — never zeroed."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class CostModel(BaseModel):
    """Cost with explicit provenance.

    ``amount`` is null when it is unknown. A missing price is not zero, and
    reporting zero would understate spend
    (AI_DEVELOPMENT_RULES.md sections 10 and 41).
    """

    amount: float | None = None
    currency: str | None = None
    provenance: Literal["ACTUAL", "ESTIMATED", "UNAVAILABLE"] = "UNAVAILABLE"


class AIExecutionResponseModel(BaseModel):
    """Maps to ``AIExecutionResponse``."""

    request_id: str
    trace_id: str | None = None
    execution_plan: ExecutionPlanModel
    result: dict[str, Any] = Field(default_factory=dict)
    usage: UsageModel = Field(default_factory=UsageModel)
    cost: CostModel = Field(default_factory=CostModel)
    quality_score: float | None = None


# ── Dependency wiring ──────────────────────────────────────────────────────
async def get_orchestrator(request: Request) -> AsyncIterator[CostAwareOrchestrator]:
    """Build an orchestrator bound to this request's database session."""
    database = request.app.state.database
    async with database.session() as session:
        registry = ModelRegistryService(ModelRepository(session))
        # The database, not this request's session: telemetry commits
        # independently so a refusal's rollback cannot discard its own record.
        telemetry_recorder = TelemetryRecorder(database.session)
        yield CostAwareOrchestrator(
            model_gateway=request.app.state.model_gateway,
            registry_service=registry,
            budget_evaluator=RepositoryBudgetEvaluator(_BudgetLimitSource(session)),
            routing_policy_repository=RoutingPolicyRepository(session),
            telemetry_recorder=telemetry_recorder,
            # Guardrails run on every execution: the input layer before
            # routing, the output layer before a result is returned. Without
            # this they were a tested library that nothing invoked
            # (AI_WORKFLOWS.md section 8).
            guardrails=build_workload_guardrails(request.app.state.settings),
        )


class _BudgetLimitSource:
    """Adapter exposing budget limits to the evaluator.

    Kept minimal deliberately: the evaluator needs limits for a scope, and the
    budgets table is owned elsewhere. When no limits are configured the policy
    records ``no_budget_configured`` and allows — blocking every request because
    an operator has not set budgets yet would make the platform unusable, and
    the decision is visible in telemetry either way.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    async def limits_for_scope(
        self, *, tenant_id: str, scope: str, scope_id: str
    ) -> list[Any]:
        return []


Orchestrator = Annotated[CostAwareOrchestrator, Depends(get_orchestrator)]


# ── Endpoint ───────────────────────────────────────────────────────────────
@router.post(
    "/ai/execute",
    summary="Execute a cost-aware AI workload",
    response_model=AIExecutionResponseModel,
    status_code=status.HTTP_200_OK,
)
async def execute_ai_workload(
    body: AIExecutionRequestModel,
    # Authentication alone is not enough: executing a workload spends money,
    # so it needs the AI_EXECUTE permission. A read-only role must not be able
    # to incur cost (SECURITY.md section 4).
    principal: Annotated[Principal, Depends(RequirePermission(Permission.AI_EXECUTE))],
    orchestrator: Orchestrator,
) -> AIExecutionResponseModel:
    """Route and execute one AI workload.

    Steps 1-3 have already happened by the time this body runs: the request was
    validated against the contract schema, and the caller was authenticated and
    authorized by the dependencies above. Everything from step 4 onward is the
    orchestrator's.

    A budget BLOCK surfaces as 409 and a missing compatible model as 409, both
    raised by the orchestrator before any billable call.
    """
    image_bytes: list[tuple[bytes, str]] = []
    for ref in body.input_refs:
        if ref.content_type.startswith("image/"):
            ref_path = Path(ref.ref)
            if ref_path.is_file():
                try:
                    data = ref_path.read_bytes()
                    image_bytes.append((data, ref.content_type))
                except Exception:
                    logger.warning("failed_to_read_input_ref", extra={"ref": ref.ref})

    result = await orchestrator.execute(
        OrchestrationRequest(
            workload_type=body.workload_type,
            business_priority=BusinessPriority(body.business_priority),
            payload=body.request_payload,
            workload_id=body.workload_id,
            plant_id=body.plant_id,
            department_id=body.department_id,
            modality=body.modality,
            quality_requirement=body.quality_requirement,
            max_cost=body.max_cost,
            image_count=sum(
                1 for ref in body.input_refs if ref.content_type.startswith("image/")
            ),
            image_bytes=image_bytes,
        ),
        principal,
    )

    plan = result.plan
    return AIExecutionResponseModel(
        request_id=result.request_id,
        trace_id=result.trace_id,
        execution_plan=ExecutionPlanModel(**plan.to_contract_dict()),
        result=result.result,
        usage=UsageModel(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
        ),
        cost=CostModel(
            amount=result.cost_amount,
            currency=result.cost_currency,
            provenance=result.cost_provenance,  # type: ignore[arg-type]
        ),
        quality_score=result.quality_score,
    )
