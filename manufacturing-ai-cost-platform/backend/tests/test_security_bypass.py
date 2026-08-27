"""Bypass tests: budget, policy and tenant controls cannot be talked around.

These target the controls an attacker — or a confused caller, or an LLM under
the influence of injected content — would try to go around rather than through.

AI_DEVELOPMENT_RULES.md sections 10, 11 and 18 are the rules under test:
LLMs may recommend, deterministic policy code authorizes; a budget rule is
deterministic and server-side; an LLM can never bypass security, budget,
authorization or approval controls.

None of these expectations may be relaxed to get a green run.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.base import Base
from app.db.models.registry import ModelRegistryEntry
from app.main import create_app
from app.orchestrator import (
    BudgetBlockedError,
    BusinessPriority,
    CostAwareOrchestrator,
    NullBudgetEvaluator,
    OrchestrationRequest,
    StaticBudgetEvaluator,
)
from app.policies.budget_policy import PolicyOutcome
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Principal, Role, RoleAssignment, ScopeType

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


def _principal(*roles: Role, tenant: str = TENANT_A) -> Principal:
    return Principal(
        subject="user-1",
        tenant_id=tenant,
        assignments=tuple(RoleAssignment(r, ScopeType.TENANT, tenant) for r in roles),
    )


@pytest.fixture
def adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.database.session() as session:
            session.add(
                ModelRegistryEntry(
                    id=str(uuid.uuid4()),
                    model_name="test-reasoning-model",
                    provider="genailab",
                    capability="reasoning",
                    enabled=True,
                )
            )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


# ===========================================================================
# Budget bypass
# ===========================================================================
class _FakeRegistry:
    def __init__(self, entries: list[ModelRegistryEntry]) -> None:
        self.entries = entries
        self.queried = False

    async def find_for_workload(self, *_: Any, **__: Any) -> list[ModelRegistryEntry]:
        self.queried = True
        return list(self.entries)


def _model() -> ModelRegistryEntry:
    return ModelRegistryEntry(
        id=str(uuid.uuid4()),
        model_name="m1",
        provider="genailab",
        capability="reasoning",
        enabled=True,
    )


def _request(**overrides: Any) -> OrchestrationRequest:
    defaults: dict[str, Any] = {
        "workload_type": "predictive_maintenance",
        "business_priority": BusinessPriority.NORMAL,
        "payload": {"sensor": "ok"},
    }
    defaults.update(overrides)
    return OrchestrationRequest(**defaults)


async def test_a_blocked_budget_cannot_be_talked_past() -> None:
    """BLOCK stops the request before any billable call, every time."""
    from app.integrations.llm.client import MockModelGateway

    gateway = MockModelGateway()
    orchestrator = CostAwareOrchestrator(
        model_gateway=gateway,
        registry_service=_FakeRegistry([_model()]),
        budget_evaluator=StaticBudgetEvaluator(PolicyOutcome.BLOCK),
    )

    for _ in range(3):
        with pytest.raises(BudgetBlockedError):
            await orchestrator.execute(_request(), _principal(Role.AI_ENGINEER))

    assert gateway.call_count == 0


async def test_a_high_priority_request_does_not_escape_a_block() -> None:
    """Priority influences routing, not whether the budget applies."""
    from app.integrations.llm.client import MockModelGateway

    gateway = MockModelGateway()
    orchestrator = CostAwareOrchestrator(
        model_gateway=gateway,
        registry_service=_FakeRegistry([_model()]),
        budget_evaluator=StaticBudgetEvaluator(PolicyOutcome.BLOCK),
    )

    with pytest.raises(BudgetBlockedError):
        await orchestrator.execute(
            _request(business_priority=BusinessPriority.CRITICAL),
            _principal(Role.ADMIN),
        )
    assert gateway.call_count == 0


async def test_a_blocked_budget_skips_routing_work_entirely() -> None:
    """Nothing downstream of the budget check runs, so nothing can override it."""
    from app.integrations.llm.client import MockModelGateway

    registry = _FakeRegistry([_model()])
    orchestrator = CostAwareOrchestrator(
        model_gateway=MockModelGateway(),
        registry_service=registry,
        budget_evaluator=StaticBudgetEvaluator(PolicyOutcome.BLOCK),
    )

    with pytest.raises(BudgetBlockedError):
        await orchestrator.execute(_request(), _principal(Role.AI_ENGINEER))

    assert registry.queried is False


async def test_the_client_cannot_supply_its_own_budget_status(
    client: AsyncClient, api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """budget_status is an outcome the server computes, not an input.

    The request schema forbids unknown fields, so an attempt to assert one is
    refused rather than silently ignored.
    """
    token = adapter.issue_token(
        subject="u1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json={
            "workload_type": "predictive_maintenance",
            "business_priority": "NORMAL",
            "budget_status": "ALLOW",
        },
        headers=_auth(token),
    )
    assert response.status_code == 422


async def test_the_client_cannot_supply_an_execution_plan(
    client: AsyncClient, api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """The plan is the server's decision record, never a caller's instruction."""
    token = adapter.issue_token(
        subject="u1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json={
            "workload_type": "predictive_maintenance",
            "business_priority": "NORMAL",
            "execution_plan": {"selected_model_id": "anything-i-want"},
        },
        headers=_auth(token),
    )
    assert response.status_code == 422


async def test_a_client_cannot_pin_the_model(
    client: AsyncClient, api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """Model selection belongs to the routing policy, not the caller.

    Otherwise a caller could route around a downgrade by naming an expensive
    model directly.
    """
    token = adapter.issue_token(
        subject="u1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json={
            "workload_type": "predictive_maintenance",
            "business_priority": "NORMAL",
            "model": "expensive-model",
        },
        headers=_auth(token),
    )
    assert response.status_code == 422


# ===========================================================================
# Policy bypass
# ===========================================================================
async def test_applying_an_unapproved_recommendation_is_refused(
    client: AsyncClient, api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """A recommendation must pass approval before it becomes a live policy.

    SECURITY.md section 15: optimization recommendations are not production
    changes.
    """
    token = adapter.issue_token(
        subject="admin",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.post(
        f"{api_prefix}/optimization/does-not-exist/apply",
        json={},
        headers=_auth(token),
    )
    # Never a 200: an unknown or unapproved recommendation cannot be activated.
    assert response.status_code in (404, 409)


async def test_optimization_endpoints_require_authentication(
    client: AsyncClient, api_prefix: str
) -> None:
    """Every lifecycle action is protected, not just the read."""
    for path, payload in [
        ("/optimization/recommendations", None),
        ("/optimization/rec-1/approve", {}),
        ("/optimization/rec-1/apply", {}),
        ("/optimization/rec-1/rollback", {}),
        ("/optimization/simulate", {"request_volume": 10}),
    ]:
        if payload is None:
            response = await client.get(f"{api_prefix}{path}")
        else:
            response = await client.post(f"{api_prefix}{path}", json=payload)
        assert response.status_code == 401, path


async def test_a_simulation_cannot_activate_anything(
    client: AsyncClient, api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """The simulator is read-only. Running it must not create a policy.

    This is the LLM-cannot-activate-a-policy rule at the API boundary
    (AI_DEVELOPMENT_RULES.md section 12).
    """
    token = adapter.issue_token(
        subject="admin",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )

    before = await client.get(
        f"{api_prefix}/optimization/recommendations", headers=_auth(token)
    )
    simulate = await client.post(
        f"{api_prefix}/optimization/simulate",
        json={"request_volume": 1_000, "budget_amount": 500},
        headers=_auth(token),
    )
    after = await client.get(
        f"{api_prefix}/optimization/recommendations", headers=_auth(token)
    )

    assert simulate.status_code == 200
    assert simulate.json()["provenance"] == "SIMULATED"
    # No recommendation was created as a side effect.
    assert after.json()["page"]["total"] == before.json()["page"]["total"]


# ===========================================================================
# Tenant bypass at the execution boundary
# ===========================================================================
async def test_the_orchestrator_ignores_a_client_supplied_tenant() -> None:
    """Tenant comes from the token. A body field cannot move an execution."""
    from app.integrations.llm.client import MockModelGateway

    orchestrator = CostAwareOrchestrator(
        model_gateway=MockModelGateway(),
        registry_service=_FakeRegistry([_model()]),
        budget_evaluator=NullBudgetEvaluator(),
    )
    result = await orchestrator.execute(
        _request(plant_id="plant-belonging-to-b"),
        _principal(Role.AI_ENGINEER, tenant=TENANT_A),
    )
    assert result.plan.tenant_id == TENANT_A


async def test_telemetry_records_the_authenticated_tenant_not_the_request() -> None:
    """An execution attributed to the wrong tenant would corrupt every cost
    report built on it."""
    from app.integrations.llm.client import MockModelGateway

    class Recorder:
        def __init__(self) -> None:
            self.records: list[Any] = []

        async def record_execution(self, *, plan: Any, outcome: str, **_: Any) -> None:
            self.records.append(plan)

    recorder = Recorder()
    orchestrator = CostAwareOrchestrator(
        model_gateway=MockModelGateway(),
        registry_service=_FakeRegistry([_model()]),
        budget_evaluator=NullBudgetEvaluator(),
        telemetry_recorder=recorder,
    )
    await orchestrator.execute(_request(), _principal(Role.AI_ENGINEER, tenant=TENANT_A))

    assert recorder.records[0].tenant_id == TENANT_A
    assert recorder.records[0].user_id == "user-1"
