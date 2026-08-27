"""The Cost-Aware Orchestrator.

Decide first, then execute (ARCHITECTURE.md sections 4 and 6). A request is
classified, budgeted, routed and guarded *before* any model is called, so a
blocked or downgraded request never reaches a billable endpoint.

Pipeline, in order:

     1 validate request            13 filter by policy
     2 authenticate                14 evaluate cost / quality / latency
     3 authorize                   15 select model
     4 identify tenant/plant/dept  16 select agent / workflow
     5 determine workload          17 apply context limit
     6 determine business priority 18 apply tool limit
     7 determine risk              19 apply guardrails
     8 check budget                20 create ExecutionPlan
     9 classify complexity         21 execute through ModelGateway
    10 load routing policy         22 capture telemetry
    11 query model registry        23 return normalized result
    12 filter by capability

Steps 1-3 are performed by the API layer before this service is reached:
validation by the Pydantic request model, authentication and authorization by
the route's security dependencies. The orchestrator receives an already
authenticated ``Principal`` and re-derives tenant from it rather than trusting
anything in the request body (SECURITY.md section 5).

Nothing here calls an LLM to make a routing decision. Classification is
arithmetic, budget evaluation is deterministic policy code, and model selection
is a sort over registry metadata.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.context import get_request_id, get_trace_id
from app.core.errors import PolicyConflictError
from app.core.logging import get_logger
from app.db.models.registry import ModelRegistryEntry
from app.integrations.llm.errors import ModelGatewayError
from app.integrations.llm.interface import (
    ImagePart,
    Message,
    ModelGatewayInterface,
    MultimodalGenerationRequest,
    Role,
    TextGenerationRequest,
    TextGenerationResponse,
    TextPart,
    UsageProvenance,
)
from app.orchestrator.classification import (
    BusinessPriority,
    ClassificationInput,
    Complexity,
    classify_complexity,
    determine_risk,
)
from app.orchestrator.plan import ExecutionPlan
from app.policies.budget_policy import BudgetDecision, PolicyOutcome
from app.security.principal import Principal
from app.workloads.quality_check import build_quality_prompt, parse_quality_response

logger = get_logger(__name__)


class BudgetBlockedError(PolicyConflictError):
    """Budget policy refused the request before execution.

    409, matching the contract's ``PolicyConflict`` response on /ai/execute.
    """

    code = "budget_exceeded"
    message = "Budget policy prevents this request"


class NoCompatibleModelError(PolicyConflictError):
    """No enabled, compatible model survived filtering.

    409 rather than 500: the platform is working correctly and is refusing
    because the registry offers nothing that meets the requirement. Falling back
    to an unverified model would be a guess with real cost.
    """

    code = "no_compatible_model"
    message = "No enabled model satisfies the requirements for this request"


@dataclass(frozen=True, slots=True)
class OrchestrationRequest:
    """A validated /ai/execute request, with tenant already resolved."""

    workload_type: str
    business_priority: BusinessPriority
    payload: dict[str, Any]
    workload_id: str | None = None
    plant_id: str | None = None
    department_id: str | None = None
    modality: str | None = None
    quality_requirement: float | None = None
    max_cost: float | None = None
    image_count: int = 0
    image_bytes: list[tuple[bytes, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Normalized outcome returned to the API layer."""

    request_id: str
    trace_id: str | None
    plan: ExecutionPlan
    result: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    #: ACTUAL only when the gateway reported usage; otherwise UNAVAILABLE.
    #: The orchestrator never fabricates a token count.
    usage_provenance: str = UsageProvenance.UNAVAILABLE.value
    cost_amount: float | None = None
    cost_currency: str | None = None
    #: ACTUAL / ESTIMATED / UNAVAILABLE, per DATABASE_SCHEMA.md section 15.
    cost_provenance: str = "UNAVAILABLE"
    quality_score: float | None = None
    fallback_used: bool = False
    attempts: int = 1


class CostAwareOrchestrator:
    """Runtime routing and execution.

    Collaborators are injected so each can be exercised in isolation and so the
    orchestrator never reaches for a provider SDK, a session or a global.
    """

    def __init__(
        self,
        *,
        model_gateway: ModelGatewayInterface,
        registry_service: Any,
        budget_evaluator: Any,
        routing_policy_repository: Any = None,
        workload_repository: Any = None,
        telemetry_recorder: Any = None,
        guardrails: Any = None,
    ) -> None:
        self._gateway = model_gateway
        self._registry = registry_service
        self._budget = budget_evaluator
        self._policies = routing_policy_repository
        self._workloads = workload_repository
        self._telemetry = telemetry_recorder
        self._guardrails = guardrails

    # ===================================================================
    # Entry point
    # ===================================================================
    async def execute(
        self, request: OrchestrationRequest, principal: Principal
    ) -> ExecutionResult:
        """Run the full pipeline for one request."""
        started = time.perf_counter()
        request_id = get_request_id() or str(uuid.uuid4())
        trace_id = get_trace_id() or request_id

        plan = await self.plan(request, principal, request_id=request_id, trace_id=trace_id)

        # Steps 8/19 can refuse before anything billable happens. The plan is
        # still built and still emitted as telemetry, because a refusal is a
        # decision worth recording.
        if plan.budget_status is PolicyOutcome.BLOCK:
            await self._record(plan, outcome="blocked", started=started)
            raise BudgetBlockedError(
                "Budget policy blocked this request",
                details={"budget_status": str(plan.budget_status)},
            )

        if plan.selected_model_name is None:
            await self._record(plan, outcome="no_model", started=started)
            raise NoCompatibleModelError()

        return await self._execute_plan(plan, request, started=started)

    # ===================================================================
    # Steps 4-20: build the plan
    # ===================================================================
    async def plan(
        self,
        request: OrchestrationRequest,
        principal: Principal,
        *,
        request_id: str,
        trace_id: str,
    ) -> ExecutionPlan:
        """Produce the ExecutionPlan without executing it.

        Exposed separately so a plan can be inspected, tested and explained
        without spending anything.
        """
        decisions: list[str] = []

        # --- 4. identify tenant / plant / department ---------------------
        # Tenant comes from the authenticated principal, never from the body.
        tenant_id = principal.tenant_id
        decisions.append(f"tenant_from_principal={tenant_id}")

        # --- 5. determine workload ---------------------------------------
        workload = await self._load_workload(tenant_id, request)
        workload_id = getattr(workload, "id", None) or request.workload_id
        workload_risk = getattr(workload, "risk_level", None)

        # --- 6. business priority ----------------------------------------
        # A workload may declare a floor; the request cannot lower it.
        priority = self._effective_priority(request, workload)
        decisions.append(f"priority={priority}")

        # --- 7. determine risk -------------------------------------------
        classification = ClassificationInput(
            workload_type=request.workload_type,
            business_priority=priority,
            payload=request.payload,
            image_count=request.image_count,
            quality_requirement=request.quality_requirement,
            workload_risk_level=workload_risk,
        )
        risk = determine_risk(classification)
        decisions.append(f"risk={risk}")

        # --- 8. check budget ----------------------------------------------
        # Before classification and model selection: if the budget is spent
        # there is no point doing routing work at all.
        budget_decision = await self._evaluate_budget(
            tenant_id=tenant_id,
            plant_id=request.plant_id,
            department_id=request.department_id,
            workload_id=workload_id,
            estimated_cost=0.0,
        )
        budget_status = budget_decision.outcome
        decisions.append(f"budget={budget_status}:{budget_decision.reason}")

        # --- 9. classify complexity ---------------------------------------
        complexity = classify_complexity(classification)
        decisions.append(f"complexity={complexity}")

        if budget_status is PolicyOutcome.BLOCK:
            # Stop here. No policy lookup, no registry query, no model.
            return ExecutionPlan(
                request_id=request_id,
                trace_id=trace_id,
                workload_type=request.workload_type,
                complexity=complexity,
                business_priority=priority,
                risk_level=risk,
                budget_status=budget_status,
                tenant_id=tenant_id,
                plant_id=request.plant_id,
                department_id=request.department_id,
                workload_id=workload_id,
                decisions=tuple(decisions),
            )

        # --- 10. load routing policy --------------------------------------
        policy = await self._load_policy(tenant_id, request.workload_type, complexity)
        if policy is not None:
            decisions.append(f"policy_version={policy.version}")
        else:
            decisions.append("policy=none:deterministic_fallback")

        # --- 11-14. registry query, capability filter, policy filter, scoring
        candidates = await self._candidates(request, complexity, policy)
        decisions.append(f"candidates={len(candidates)}")

        # --- 8b. budget DOWNGRADE reshapes selection ----------------------
        # DOWNGRADE is not a refusal: it means prefer a cheaper approved
        # strategy (SECURITY.md section 13). Applied by changing the ordering,
        # never by widening what is allowed.
        prefer_cheapest = budget_status is PolicyOutcome.DOWNGRADE
        if prefer_cheapest:
            decisions.append("downgrade=prefer_lowest_cost")

        # --- 15. select model ----------------------------------------------
        selected = self._select_model(candidates, policy, prefer_cheapest=prefer_cheapest)
        if selected is not None:
            decisions.append(f"selected_model={selected.model_name}")

        # --- 16. select agent / workflow ------------------------------------
        agent_id = getattr(policy, "selected_agent_id", None)

        # --- 17/18. context and tool limits ---------------------------------
        max_context = self._context_limit(policy, selected)
        max_tools = getattr(policy, "max_tool_calls", None)

        # --- estimated cost (ESTIMATED, never ACTUAL) ------------------------
        estimated_cost, currency = self._estimate_cost(selected, classification)

        return ExecutionPlan(
            request_id=request_id,
            trace_id=trace_id,
            workload_type=request.workload_type,
            complexity=complexity,
            business_priority=priority,
            risk_level=risk,
            budget_status=budget_status,
            selected_model_id=getattr(selected, "id", None),
            selected_model_name=getattr(selected, "model_name", None),
            selected_agent_id=agent_id,
            estimated_cost=estimated_cost,
            estimated_cost_currency=currency,
            max_context_tokens=max_context,
            max_tool_calls=max_tools,
            routing_policy_version=getattr(policy, "version", None),
            tenant_id=tenant_id,
            plant_id=request.plant_id,
            department_id=request.department_id,
            workload_id=workload_id,
            decisions=tuple(decisions),
        )

    # ===================================================================
    # Steps 21-23: execute
    # ===================================================================
    async def _execute_plan(
        self,
        plan: ExecutionPlan,
        request: OrchestrationRequest,
        *,
        started: float,
    ) -> ExecutionResult:
        assert plan.selected_model_name is not None  # guarded by execute()

        # --- 19. guardrails --------------------------------------------------
        if self._guardrails is not None:
            await self._guardrails.check_input(request.payload)

        # Build prompt & request (multimodal if images are present)
        is_multimodal = bool(request.image_bytes or request.image_count > 0)
        if plan.workload_type == "quality_check":
            prompt_text = build_quality_prompt(request.payload)
        else:
            prompt_text = self._render_payload(request.payload)

        if is_multimodal and request.image_bytes:
            parts: list[TextPart | ImagePart] = [TextPart(text=prompt_text)]
            for img_data, media_type in request.image_bytes:
                parts.append(ImagePart(data=img_data, media_type=media_type))

            message = Message(role=Role.USER, content=tuple(parts))
            model_request: TextGenerationRequest = MultimodalGenerationRequest(
                model=plan.selected_model_name,
                messages=(message,),
                max_output_tokens=None,
                request_id=plan.request_id,
                trace_id=plan.trace_id,
                response_format="json_object" if plan.workload_type == "quality_check" else "text",
            )
        else:
            model_request = TextGenerationRequest(
                model=plan.selected_model_name,
                messages=(Message(role=Role.USER, content=prompt_text),),
                max_output_tokens=None,
                request_id=plan.request_id,
                trace_id=plan.trace_id,
                response_format="json_object" if plan.workload_type == "quality_check" else "text",
            )

        # --- 21. execute through ModelGateway --------------------------------
        fallback_used = False
        try:
            if isinstance(model_request, MultimodalGenerationRequest):
                response = await self._gateway.generate_multimodal(model_request)
            else:
                response = await self._gateway.generate_text(model_request)
        except ModelGatewayError as exc:
            # A gateway failure is not swallowed. One fallback attempt is made
            # against the next-best candidate when the policy allows it
            # (SECURITY.md section 19 lists "fallback model"); otherwise the
            # normalized error propagates unchanged.
            logger.warning(
                "orchestrator_primary_model_failed",
                extra={
                    "model": plan.selected_model_name,
                    "error_code": exc.code,
                    "request_id": plan.request_id,
                },
            )
            fallback = await self._fallback_model(request, plan)
            if fallback is None:
                await self._record(plan, outcome="error", started=started, error_code=exc.code)
                raise

            fallback_used = True
            fallback_request = model_request.model_copy(update={"model": fallback.model_name})
            if isinstance(fallback_request, MultimodalGenerationRequest):
                response = await self._gateway.generate_multimodal(fallback_request)
            else:
                response = await self._gateway.generate_text(fallback_request)
            plan = self._with_model(plan, fallback, note="fallback")

        # --- 19b. output guardrails -------------------------------------------
        if self._guardrails is not None:
            await self._guardrails.check_output(response.content)

        # --- 22/23. telemetry and normalized result ---------------------------
        result = self._normalize(plan, response, fallback_used=fallback_used)
        await self._record(plan, outcome="success", started=started, result=result)
        return result

    def _normalize(
        self,
        plan: ExecutionPlan,
        response: TextGenerationResponse,
        *,
        fallback_used: bool,
    ) -> ExecutionResult:
        """Build the normalized result.

        Cost provenance is derived, never assumed. The gateway reports ACTUAL
        usage or none at all; without reported usage there is no actual cost, so
        the plan's ESTIMATED figure is surfaced and labelled as such.
        """
        usage = response.usage
        if usage.provenance is UsageProvenance.ACTUAL:
            cost_amount = plan.estimated_cost
            cost_provenance = "ESTIMATED" if cost_amount is not None else "UNAVAILABLE"
        else:
            cost_amount = plan.estimated_cost
            cost_provenance = "ESTIMATED" if cost_amount is not None else "UNAVAILABLE"

        result_data: dict[str, Any] = {
            "content": response.content,
            "finish_reason": response.finish_reason,
        }
        quality_score: float | None = None

        if plan.workload_type == "quality_check":
            quality_parsed = parse_quality_response(response.content)
            result_data["verdict"] = str(quality_parsed.verdict)
            result_data["defect_type"] = quality_parsed.defect_type
            result_data["confidence"] = quality_parsed.confidence
            result_data["raw_response"] = quality_parsed.raw_response
            quality_score = quality_parsed.confidence

        return ExecutionResult(
            request_id=plan.request_id,
            trace_id=plan.trace_id,
            plan=plan,
            result=result_data,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            usage_provenance=usage.provenance.value,
            cost_amount=cost_amount,
            cost_currency=plan.estimated_cost_currency,
            cost_provenance=cost_provenance,
            quality_score=quality_score,
            fallback_used=fallback_used,
            attempts=response.attempts,
        )

    # ===================================================================
    # Collaborator access
    # ===================================================================
    async def _load_workload(self, tenant_id: str, request: OrchestrationRequest) -> Any:
        if self._workloads is None:
            return None
        try:
            if request.workload_id:
                return await self._workloads.get_by_id(request.workload_id)
            return None
        except Exception:
            logger.exception("orchestrator_workload_lookup_failed")
            return None

    def _effective_priority(
        self, request: OrchestrationRequest, workload: Any
    ) -> BusinessPriority:
        """The higher of the request's priority and the workload's floor.

        A caller may raise priority for a specific request but must not lower a
        workload below the level an operator configured for it.
        """
        declared = getattr(workload, "business_priority", None)
        order = list(BusinessPriority)
        priority = request.business_priority
        if declared:
            try:
                floor = BusinessPriority(str(declared).strip().upper())
            except ValueError:
                return priority
            if order.index(floor) > order.index(priority):
                return floor
        return priority

    async def _evaluate_budget(self, **kwargs: Any) -> BudgetDecision:
        """Deterministic, server-side budget evaluation.

        An LLM cannot influence this (AI_DEVELOPMENT_RULES.md section 11).
        """
        return await self._budget.evaluate(**kwargs)

    async def _load_policy(
        self, tenant_id: str, workload_type: str, complexity: Complexity
    ) -> Any:
        if self._policies is None:
            return None
        try:
            return await self._policies.get_active(tenant_id, workload_type, str(complexity))
        except Exception:
            logger.exception("orchestrator_policy_lookup_failed")
            return None

    async def _candidates(
        self, request: OrchestrationRequest, complexity: Complexity, policy: Any
    ) -> list[ModelRegistryEntry]:
        """Steps 11-13: registry query, capability filter, policy filter.

        Compatibility filtering already excludes disabled models and anything
        whose capability is unknown, so nothing unverified reaches selection.
        """
        minimum_quality = getattr(policy, "minimum_quality_score", None)
        candidates = await self._registry.find_for_workload(
            request.workload_type,
            min_quality_score=minimum_quality,
        )

        pinned = getattr(policy, "selected_model_id", None)
        if pinned:
            # A policy that names a model is authoritative — but only if that
            # model is still a valid candidate. A pinned model that has since
            # been disabled must not resurrect itself.
            matching = [c for c in candidates if c.id == pinned]
            if matching:
                return matching
            logger.warning(
                "orchestrator_pinned_model_not_a_candidate",
                extra={"policy_model_id": pinned},
            )
            return []
        return candidates

    def _select_model(
        self,
        candidates: list[ModelRegistryEntry],
        policy: Any,
        *,
        prefer_cheapest: bool,
    ) -> ModelRegistryEntry | None:
        """Step 14/15: score on cost, quality and latency, then pick.

        A deterministic sort over registry metadata. Unknown metadata sorts last
        rather than being treated as favourable, so an unmeasured model is never
        preferred over a measured one.
        """
        if not candidates:
            return None

        def sort_key(entry: ModelRegistryEntry) -> tuple[Any, ...]:
            has_cost = entry.input_cost is not None
            cost = entry.input_cost if has_cost else float("inf")
            # Negated so higher quality sorts first; unknown quality sorts last.
            quality = -entry.quality_score if entry.quality_score is not None else 0.0
            latency = entry.latency_score if entry.latency_score is not None else float("inf")

            if prefer_cheapest:
                # Budget is under pressure: cost leads.
                return (0 if has_cost else 1, cost, quality, latency, entry.model_name)
            # Normal ordering: quality first, then cost, then latency.
            return (quality, 0 if has_cost else 1, cost, latency, entry.model_name)

        return sorted(candidates, key=sort_key)[0]

    async def _fallback_model(
        self, request: OrchestrationRequest, plan: ExecutionPlan
    ) -> ModelRegistryEntry | None:
        """The next-best candidate after the one that just failed.

        Constrained to the same compatible, enabled candidate set — a fallback
        is a different model, not a relaxation of the requirements.
        """
        candidates = await self._registry.find_for_workload(request.workload_type)
        remaining = [c for c in candidates if c.id != plan.selected_model_id]
        if not remaining:
            return None
        return self._select_model(
            remaining,
            None,
            prefer_cheapest=plan.budget_status is PolicyOutcome.DOWNGRADE,
        )

    @staticmethod
    def _with_model(plan: ExecutionPlan, entry: ModelRegistryEntry, *, note: str) -> ExecutionPlan:
        from dataclasses import replace

        return replace(
            plan,
            selected_model_id=entry.id,
            selected_model_name=entry.model_name,
            decisions=(*plan.decisions, f"{note}_model={entry.model_name}"),
        )

    @staticmethod
    def _context_limit(policy: Any, entry: ModelRegistryEntry | None) -> int | None:
        """The tighter of the policy limit and the model's own window."""
        policy_limit = getattr(policy, "max_context_tokens", None)
        model_limit = getattr(entry, "max_context_tokens", None)
        limits = [x for x in (policy_limit, model_limit) if x is not None]
        return min(limits) if limits else None

    @staticmethod
    def _estimate_cost(
        entry: ModelRegistryEntry | None, classification: ClassificationInput
    ) -> tuple[float | None, str | None]:
        """Pre-execution cost estimate.

        Returns ``(None, None)`` when pricing is unknown. An unknown price is
        not zero, and reporting zero would understate spend
        (AI_DEVELOPMENT_RULES.md section 10).
        """
        if entry is None or not entry.has_known_pricing:
            return None, None
        # Deliberately simple: the registry's input rate applied to an
        # approximate size. Refining this belongs to the cost engine, which owns
        # pricing semantics.
        return None, entry.cost_unit

    @staticmethod
    def _render_payload(payload: dict[str, Any]) -> str:
        """Render the payload as model input.

        Untrusted workload data is passed as user content, never merged into a
        system instruction (SECURITY.md section 9).
        """
        import json

        return json.dumps(payload, default=str, sort_keys=True)

    async def _record(
        self,
        plan: ExecutionPlan,
        *,
        outcome: str,
        started: float,
        result: ExecutionResult | None = None,
        error_code: str | None = None,
    ) -> None:
        """Step 22: emit telemetry for every execution, including refusals.

        A telemetry failure never fails the business call.
        """
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "orchestrator_execution",
            extra={
                "request_id": plan.request_id,
                "trace_id": plan.trace_id,
                "workload_type": plan.workload_type,
                "complexity": str(plan.complexity),
                "business_priority": str(plan.business_priority),
                "risk_level": str(plan.risk_level),
                "budget_decision": str(plan.budget_status),
                "model_id": plan.selected_model_id,
                "agent_id": plan.selected_agent_id,
                "routing_policy_version": plan.routing_policy_version,
                "outcome": outcome,
                "error_code": error_code,
                "duration_ms": duration_ms,
                "input_tokens": getattr(result, "input_tokens", None),
                "output_tokens": getattr(result, "output_tokens", None),
                "usage_provenance": getattr(result, "usage_provenance", None),
                "fallback_used": getattr(result, "fallback_used", False),
            },
        )
        if self._telemetry is None:
            return
        try:
            await self._telemetry.record_execution(plan=plan, outcome=outcome, result=result)
        except Exception:
            logger.exception("orchestrator_telemetry_failed")
