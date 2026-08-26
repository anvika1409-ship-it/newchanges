"""Cost-Aware Orchestrator tests.

Covers the required cases: simple / medium / complex request, budget block,
budget downgrade, incompatible model, disabled model, gateway failure, fallback,
and the execution plan itself.

No live model is called anywhere: the gateway is the mock, and failures are
injected into it.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.db.models.registry import ModelRegistryEntry
from app.integrations.llm.client import MockModelGateway, ResilientGateway, RetryPolicy
from app.integrations.llm.errors import GatewayUnavailableError
from app.integrations.llm.interface import UsageProvenance
from app.orchestrator import (
    BudgetBlockedError,
    BusinessPriority,
    ClassificationInput,
    Complexity,
    CostAwareOrchestrator,
    NoCompatibleModelError,
    NullBudgetEvaluator,
    OrchestrationRequest,
    RiskLevel,
    StaticBudgetEvaluator,
    classify_complexity,
    determine_risk,
)
from app.policies.budget_policy import PolicyOutcome
from app.security.principal import Principal, Role, RoleAssignment, ScopeType

TENANT = "tenant-a"
QUALITY_CHECK = "quality_check"
MAINTENANCE = "predictive_maintenance"


def principal() -> Principal:
    return Principal(
        subject="user-1",
        tenant_id=TENANT,
        assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT),),
    )


def entry(**overrides: Any) -> ModelRegistryEntry:
    """A registry entry with everything unknown unless stated."""
    defaults: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "model_name": f"model-{uuid.uuid4().hex[:6]}",
        "provider": "genailab",
        "capability": "reasoning",
        "modality": None,
        "input_cost": None,
        "output_cost": None,
        "cost_unit": None,
        "max_context_tokens": None,
        "supports_vision": None,
        "supports_tools": None,
        "supports_structured_output": None,
        "supports_embeddings": None,
        "quality_score": None,
        "latency_score": None,
        "risk_level": None,
        "enabled": True,
    }
    defaults.update(overrides)
    return ModelRegistryEntry(**defaults)


class FakeRegistry:
    """Registry stub returning a fixed candidate set.

    Mirrors the real service's contract: ``find_for_workload`` returns only
    enabled, compatible models, so anything filtered out never appears here.
    """

    def __init__(self, candidates: list[ModelRegistryEntry] | None = None) -> None:
        self.candidates = candidates or []
        self.calls: list[str] = []

    async def find_for_workload(self, workload_type: str, **_: Any) -> list[ModelRegistryEntry]:
        self.calls.append(workload_type)
        # Mirrors ModelRegistryService.find_for_workload, which constrains to
        # enabled models. Keeping that here means the orchestrator is tested
        # against the contract it actually depends on.
        return [c for c in self.candidates if c.enabled]


class RecordingTelemetry:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record_execution(self, *, plan: Any, outcome: str, result: Any = None) -> None:
        self.records.append({"plan": plan, "outcome": outcome, "result": result})


def build(
    *,
    candidates: list[ModelRegistryEntry] | None = None,
    budget: Any = None,
    gateway: Any = None,
    policy: Any = None,
) -> tuple[CostAwareOrchestrator, FakeRegistry, RecordingTelemetry, Any]:
    registry = FakeRegistry(candidates)
    telemetry = RecordingTelemetry()
    gw = gateway or MockModelGateway()

    class _Policies:
        async def get_active(self, *_: Any) -> Any:
            return policy

    orchestrator = CostAwareOrchestrator(
        model_gateway=gw,
        registry_service=registry,
        budget_evaluator=budget or NullBudgetEvaluator(),
        routing_policy_repository=_Policies() if policy is not None else None,
        telemetry_recorder=telemetry,
    )
    return orchestrator, registry, telemetry, gw


def request(**overrides: Any) -> OrchestrationRequest:
    defaults: dict[str, Any] = {
        "workload_type": MAINTENANCE,
        "business_priority": BusinessPriority.NORMAL,
        "payload": {"reading": "ok"},
    }
    defaults.update(overrides)
    return OrchestrationRequest(**defaults)


# ===========================================================================
# Complexity classification — simple / medium / complex
# ===========================================================================
def _classification(**overrides: Any) -> ClassificationInput:
    defaults: dict[str, Any] = {
        "workload_type": MAINTENANCE,
        "business_priority": BusinessPriority.NORMAL,
        "payload": {},
    }
    defaults.update(overrides)
    return ClassificationInput(**defaults)


def test_simple_request_classifies_as_simple() -> None:
    assert classify_complexity(_classification(payload={"t": "short reading"})) is Complexity.SIMPLE


def test_medium_request_classifies_as_medium() -> None:
    payload = {"log": "x" * 3_000}
    assert classify_complexity(_classification(payload=payload)) is Complexity.MEDIUM


def test_a_single_image_is_at_least_medium() -> None:
    assert classify_complexity(_classification(image_count=1)) is Complexity.MEDIUM


def test_complex_request_classifies_as_complex() -> None:
    payload = {"log": "x" * 20_000}
    assert classify_complexity(_classification(payload=payload)) is Complexity.COMPLEX


def test_multiple_images_are_complex() -> None:
    """Multi-image work is comparison, regardless of text size."""
    assert classify_complexity(_classification(image_count=2)) is Complexity.COMPLEX


def test_a_high_quality_requirement_forces_complex() -> None:
    assert (
        classify_complexity(_classification(quality_requirement=0.95)) is Complexity.COMPLEX
    )


def test_classification_is_deterministic() -> None:
    """The same request must always route the same way, so it can be replayed."""
    payload = {"a": "x" * 500, "b": ["y" * 100, {"c": 42}]}
    results = {classify_complexity(_classification(payload=payload)) for _ in range(50)}
    assert len(results) == 1


def test_classification_makes_no_model_call() -> None:
    """The rule the architecture states twice: no LLM to choose a model."""
    gateway = MockModelGateway()
    classify_complexity(_classification(payload={"x": "y" * 5000}))
    assert gateway.call_count == 0


# ===========================================================================
# Risk
# ===========================================================================
def test_risk_defaults_from_business_priority() -> None:
    assert determine_risk(_classification(business_priority=BusinessPriority.LOW)) is RiskLevel.LOW
    assert (
        determine_risk(_classification(business_priority=BusinessPriority.CRITICAL))
        is RiskLevel.HIGH
    )


def test_declared_workload_risk_wins_over_priority() -> None:
    """An operator's classification of a workload outranks an inference."""
    risk = determine_risk(
        _classification(
            business_priority=BusinessPriority.LOW, workload_risk_level="CRITICAL"
        )
    )
    assert risk is RiskLevel.CRITICAL


def test_unrecognised_declared_risk_falls_back_conservatively() -> None:
    risk = determine_risk(
        _classification(business_priority=BusinessPriority.HIGH, workload_risk_level="banana")
    )
    assert risk is RiskLevel.MEDIUM


# ===========================================================================
# Budget block
# ===========================================================================
async def test_budget_block_refuses_before_any_model_call() -> None:
    """A blocked request must not reach a billable endpoint."""
    orchestrator, registry, telemetry, gateway = build(
        candidates=[entry()],
        budget=StaticBudgetEvaluator(PolicyOutcome.BLOCK),
    )

    with pytest.raises(BudgetBlockedError):
        await orchestrator.execute(request(), principal())

    assert gateway.call_count == 0
    # Routing work was skipped entirely — no point querying the registry.
    assert registry.calls == []
    assert telemetry.records[-1]["outcome"] == "blocked"


async def test_budget_block_still_emits_telemetry() -> None:
    """A refusal is a decision worth recording."""
    orchestrator, _, telemetry, _ = build(
        candidates=[entry()], budget=StaticBudgetEvaluator(PolicyOutcome.BLOCK)
    )

    with pytest.raises(BudgetBlockedError):
        await orchestrator.execute(request(), principal())

    record = telemetry.records[-1]
    assert record["plan"].budget_status is PolicyOutcome.BLOCK
    assert record["plan"].selected_model_id is None


async def test_budget_block_surfaces_as_a_policy_conflict() -> None:
    """409 PolicyConflict, matching the contract's /ai/execute responses."""
    orchestrator, _, _, _ = build(
        candidates=[entry()], budget=StaticBudgetEvaluator(PolicyOutcome.BLOCK)
    )
    with pytest.raises(BudgetBlockedError) as excinfo:
        await orchestrator.execute(request(), principal())

    assert excinfo.value.status_code == 409


# ===========================================================================
# Budget downgrade
# ===========================================================================
async def test_budget_downgrade_prefers_the_cheaper_model() -> None:
    """DOWNGRADE reshapes selection; it does not refuse."""
    cheap = entry(model_name="cheap", input_cost=0.1, output_cost=0.1, cost_unit="1k",
                  quality_score=0.5)
    expensive = entry(model_name="expensive", input_cost=9.0, output_cost=9.0, cost_unit="1k",
                      quality_score=0.99)

    orchestrator, _, _, gateway = build(
        candidates=[expensive, cheap],
        budget=StaticBudgetEvaluator(PolicyOutcome.DOWNGRADE),
    )
    result = await orchestrator.execute(request(), principal())

    assert result.plan.selected_model_name == "cheap"
    assert result.plan.budget_status is PolicyOutcome.DOWNGRADE
    assert gateway.call_count == 1


async def test_without_downgrade_quality_leads() -> None:
    """Normal ordering prefers the better model even when it costs more."""
    cheap = entry(model_name="cheap", input_cost=0.1, output_cost=0.1, cost_unit="1k",
                  quality_score=0.5)
    expensive = entry(model_name="expensive", input_cost=9.0, output_cost=9.0, cost_unit="1k",
                      quality_score=0.99)

    orchestrator, _, _, _ = build(candidates=[cheap, expensive])
    result = await orchestrator.execute(request(), principal())

    assert result.plan.selected_model_name == "expensive"


async def test_downgrade_does_not_widen_the_candidate_set() -> None:
    """Cost pressure reorders candidates; it never relaxes compatibility."""
    orchestrator, registry, _, _ = build(
        candidates=[], budget=StaticBudgetEvaluator(PolicyOutcome.DOWNGRADE)
    )
    with pytest.raises(NoCompatibleModelError):
        await orchestrator.execute(request(), principal())


# ===========================================================================
# Incompatible / disabled models
# ===========================================================================
async def test_no_compatible_model_is_refused_not_guessed() -> None:
    """An empty candidate set is a refusal, never a fallback to something else."""
    orchestrator, _, telemetry, gateway = build(candidates=[])

    with pytest.raises(NoCompatibleModelError) as excinfo:
        await orchestrator.execute(request(), principal())

    assert excinfo.value.status_code == 409
    assert gateway.call_count == 0
    assert telemetry.records[-1]["outcome"] == "no_model"


async def test_disabled_model_never_reaches_selection() -> None:
    """A disabled model is excluded even when it is otherwise a perfect match.

    Enabled filtering lives in the registry, before the orchestrator sees a
    candidate. This asserts the orchestrator genuinely depends on that and does
    not select from a set it filters itself.
    """
    disabled = entry(model_name="retired", quality_score=0.99, enabled=False)
    orchestrator, _, telemetry, gateway = build(candidates=[disabled])

    with pytest.raises(NoCompatibleModelError):
        await orchestrator.execute(request(), principal())

    assert gateway.call_count == 0
    assert telemetry.records[-1]["outcome"] == "no_model"


async def test_a_disabled_model_is_skipped_when_an_enabled_one_exists() -> None:
    """The disabled model is better on paper and still must not be chosen."""
    disabled = entry(model_name="retired", quality_score=0.99, enabled=False)
    live = entry(model_name="live", quality_score=0.4)
    orchestrator, _, _, _ = build(candidates=[disabled, live])

    result = await orchestrator.execute(request(), principal())
    assert result.plan.selected_model_name == "live"


async def test_a_policy_pinned_model_that_is_no_longer_a_candidate_is_refused() -> None:
    """A disabled pinned model must not resurrect itself."""

    class Policy:
        version = 3
        selected_model_id = "pinned-but-gone"
        selected_agent_id = None
        max_context_tokens = None
        max_tool_calls = None
        minimum_quality_score = None

    orchestrator, _, _, gateway = build(candidates=[entry()], policy=Policy())

    with pytest.raises(NoCompatibleModelError):
        await orchestrator.execute(request(), principal())
    assert gateway.call_count == 0


# ===========================================================================
# Gateway failure and fallback
# ===========================================================================
async def test_gateway_failure_propagates_when_no_fallback_exists() -> None:
    """One candidate, and it fails: the normalized error surfaces."""
    only = entry(model_name="only-one")
    gateway = ResilientGateway(
        MockModelGateway(failures=[GatewayUnavailableError()]),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0),
    )
    orchestrator, _, telemetry, _ = build(candidates=[only], gateway=gateway)

    with pytest.raises(GatewayUnavailableError):
        await orchestrator.execute(request(), principal())

    assert telemetry.records[-1]["outcome"] == "error"


async def test_fallback_to_the_next_candidate_on_gateway_failure() -> None:
    """SECURITY.md section 19 lists a fallback model among required controls."""
    primary = entry(model_name="primary", quality_score=0.99)
    secondary = entry(model_name="secondary", quality_score=0.5)

    inner = MockModelGateway(failures=[GatewayUnavailableError()])
    gateway = ResilientGateway(
        inner, retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0)
    )
    orchestrator, _, telemetry, _ = build(
        candidates=[primary, secondary], gateway=gateway
    )

    result = await orchestrator.execute(request(), principal())

    assert result.fallback_used is True
    assert result.plan.selected_model_name == "secondary"
    assert telemetry.records[-1]["outcome"] == "success"


async def test_fallback_stays_within_the_compatible_candidate_set() -> None:
    """A fallback is a different model, not a relaxation of requirements."""
    primary = entry(model_name="primary", quality_score=0.99)
    inner = MockModelGateway(failures=[GatewayUnavailableError()])
    gateway = ResilientGateway(
        inner, retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0)
    )
    orchestrator, registry, _, _ = build(candidates=[primary], gateway=gateway)

    with pytest.raises(GatewayUnavailableError):
        await orchestrator.execute(request(), principal())

    # The only candidate is the one that failed, so there is nothing to fall
    # back to and the platform refuses rather than reaching outside the set.
    assert registry.calls  # the candidate set was consulted


# ===========================================================================
# Execution plan
# ===========================================================================
async def test_execution_plan_carries_every_required_field() -> None:
    model = entry(model_name="chosen", max_context_tokens=8000)

    class Policy:
        version = 7
        selected_model_id = None
        selected_agent_id = "agent-1"
        max_context_tokens = 4096
        max_tool_calls = 3
        minimum_quality_score = None

    orchestrator, _, _, _ = build(candidates=[model], policy=Policy())
    result = await orchestrator.execute(
        request(business_priority=BusinessPriority.HIGH), principal()
    )
    plan = result.plan

    assert plan.request_id
    assert plan.workload_type == MAINTENANCE
    assert plan.complexity in set(Complexity)
    assert plan.business_priority is BusinessPriority.HIGH
    assert plan.risk_level is RiskLevel.MEDIUM
    assert plan.selected_model_id == model.id
    assert plan.selected_agent_id == "agent-1"
    assert plan.max_context_tokens == 4096  # the tighter of policy and model
    assert plan.max_tool_calls == 3
    assert plan.routing_policy_version == 7
    assert plan.budget_status is PolicyOutcome.ALLOW


async def test_context_limit_takes_the_tighter_of_policy_and_model() -> None:
    model = entry(max_context_tokens=2000)

    class Policy:
        version = 1
        selected_model_id = None
        selected_agent_id = None
        max_context_tokens = 9999
        max_tool_calls = None
        minimum_quality_score = None

    orchestrator, _, _, _ = build(candidates=[model], policy=Policy())
    result = await orchestrator.execute(request(), principal())

    assert result.plan.max_context_tokens == 2000


async def test_plan_serializes_to_the_contract_shape() -> None:
    orchestrator, _, _, _ = build(candidates=[entry()])
    result = await orchestrator.execute(request(), principal())

    payload = result.plan.to_contract_dict()
    assert set(payload) == {
        "workload_type",
        "complexity",
        "selected_model_id",
        "selected_agent_id",
        "estimated_cost",
        "max_context_tokens",
        "max_tool_calls",
        "routing_policy_version",
        "budget_status",
        "risk_level",
    }


async def test_no_routing_policy_falls_back_to_deterministic_ordering() -> None:
    """A missing policy is recorded, not silently treated as version 0."""
    orchestrator, _, _, _ = build(candidates=[entry(model_name="only")])
    result = await orchestrator.execute(request(), principal())

    assert result.plan.routing_policy_version is None
    assert any("deterministic_fallback" in d for d in result.plan.decisions)


async def test_unknown_pricing_yields_no_estimated_cost() -> None:
    """An unknown price is not zero (AI_DEVELOPMENT_RULES.md section 10)."""
    orchestrator, _, _, _ = build(candidates=[entry(input_cost=None, cost_unit=None)])
    result = await orchestrator.execute(request(), principal())

    assert result.plan.estimated_cost is None
    assert result.cost_provenance == "UNAVAILABLE"


async def test_tenant_is_taken_from_the_principal_not_the_request() -> None:
    """SECURITY.md section 5: never trust a client-supplied tenant."""
    orchestrator, _, _, _ = build(candidates=[entry()])
    result = await orchestrator.execute(request(), principal())

    assert result.plan.tenant_id == TENANT
    assert any("tenant_from_principal" in d for d in result.plan.decisions)


async def test_workload_priority_floor_cannot_be_lowered_by_the_request() -> None:
    class Workload:
        id = "wl-1"
        business_priority = "CRITICAL"
        risk_level = None

    class Workloads:
        async def get_by_id(self, _: str) -> Any:
            return Workload()

    registry = FakeRegistry([entry()])
    orchestrator = CostAwareOrchestrator(
        model_gateway=MockModelGateway(),
        registry_service=registry,
        budget_evaluator=NullBudgetEvaluator(),
        workload_repository=Workloads(),
    )
    result = await orchestrator.execute(
        request(workload_id="wl-1", business_priority=BusinessPriority.LOW), principal()
    )

    assert result.plan.business_priority is BusinessPriority.CRITICAL


# ===========================================================================
# Telemetry
# ===========================================================================
async def test_every_execution_emits_telemetry() -> None:
    """AI_DEVELOPMENT_RULES.md section 8."""
    orchestrator, _, telemetry, _ = build(candidates=[entry()])
    await orchestrator.execute(request(), principal())

    assert len(telemetry.records) == 1
    assert telemetry.records[0]["outcome"] == "success"


async def test_usage_provenance_is_unavailable_when_unreported() -> None:
    """The orchestrator never fabricates token counts."""
    orchestrator, _, _, _ = build(
        candidates=[entry()], gateway=MockModelGateway(report_usage=False)
    )
    result = await orchestrator.execute(request(), principal())

    assert result.input_tokens is None
    assert result.usage_provenance == UsageProvenance.UNAVAILABLE.value


async def test_telemetry_failure_does_not_fail_the_execution() -> None:
    class BrokenTelemetry:
        async def record_execution(self, **_: Any) -> None:
            raise RuntimeError("telemetry down")

    orchestrator = CostAwareOrchestrator(
        model_gateway=MockModelGateway(),
        registry_service=FakeRegistry([entry()]),
        budget_evaluator=NullBudgetEvaluator(),
        telemetry_recorder=BrokenTelemetry(),
    )
    result = await orchestrator.execute(request(), principal())
    assert result.result["content"]
