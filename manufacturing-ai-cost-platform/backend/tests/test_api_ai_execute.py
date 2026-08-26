"""POST /api/v1/ai/execute endpoint tests.

Drives the real application with authentication active, so the route's
steps 1-3 (validate, authenticate, authorize) are genuinely exercised.
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
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType

TENANT = "tenant-a"


@pytest.fixture
def adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest.fixture
def token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="engineer-1",
        tenant_id=TENANT,
        assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT),),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _reasoning_model(**overrides: Any) -> ModelRegistryEntry:
    defaults: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "model_name": "test-reasoning-model",
        "provider": "genailab",
        "capability": "reasoning",
        "enabled": True,
    }
    defaults.update(overrides)
    return ModelRegistryEntry(**defaults)


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """The real app with a schema and one enabled reasoning model registered."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with app.state.database.session() as session:
            session.add(_reasoning_model())

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


def _body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workload_type": "predictive_maintenance",
        "business_priority": "NORMAL",
        "request_payload": {"sensor": "vibration ok"},
    }
    payload.update(overrides)
    return payload


# ===========================================================================
# Steps 1-3: validate, authenticate, authorize
# ===========================================================================
async def test_execute_requires_authentication(client: AsyncClient, api_prefix: str) -> None:
    response = await client.post(f"{api_prefix}/ai/execute", json=_body())
    assert response.status_code == 401


async def test_invalid_workload_type_is_rejected(
    client: AsyncClient, api_prefix: str, token: str
) -> None:
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(workload_type="not_a_workload"),
        headers=_auth(token),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_missing_business_priority_is_rejected(
    client: AsyncClient, api_prefix: str, token: str
) -> None:
    """business_priority is required by the contract."""
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json={"workload_type": "predictive_maintenance"},
        headers=_auth(token),
    )
    assert response.status_code == 422


async def test_unknown_field_is_rejected(
    client: AsyncClient, api_prefix: str, token: str
) -> None:
    """The contract's request shape is closed, so typos surface immediately."""
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(buisness_priority="HIGH"),
        headers=_auth(token),
    )
    assert response.status_code == 422


# ===========================================================================
# Successful execution
# ===========================================================================
async def test_successful_execution_returns_the_contract_shape(
    client: AsyncClient, api_prefix: str, token: str
) -> None:
    response = await client.post(
        f"{api_prefix}/ai/execute", json=_body(), headers=_auth(token)
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body) == {
        "request_id",
        "trace_id",
        "execution_plan",
        "result",
        "usage",
        "cost",
        "quality_score",
    }


async def test_response_carries_a_full_execution_plan(
    client: AsyncClient, api_prefix: str, token: str
) -> None:
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(business_priority="HIGH"),
        headers=_auth(token),
    )
    plan = response.json()["execution_plan"]

    assert set(plan) == {
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
    assert plan["workload_type"] == "predictive_maintenance"
    assert plan["complexity"] in {"SIMPLE", "MEDIUM", "COMPLEX"}
    assert plan["budget_status"] in {"ALLOW", "DOWNGRADE", "REQUIRE_APPROVAL", "BLOCK"}
    assert plan["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert plan["selected_model_id"] is not None


async def test_the_request_id_is_echoed_in_the_header_and_body(
    client: AsyncClient, api_prefix: str, token: str
) -> None:
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(),
        headers={**_auth(token), "X-Request-ID": "exec-req-1"},
    )
    assert response.headers["X-Request-ID"] == "exec-req-1"
    assert response.json()["request_id"] == "exec-req-1"


async def test_unknown_cost_is_null_not_zero(
    client: AsyncClient, api_prefix: str, token: str
) -> None:
    """The seeded model has no pricing, so cost is unknown — not free."""
    response = await client.post(
        f"{api_prefix}/ai/execute", json=_body(), headers=_auth(token)
    )
    cost = response.json()["cost"]

    assert cost["amount"] is None
    assert cost["provenance"] == "UNAVAILABLE"


async def test_a_larger_payload_classifies_as_more_complex(
    client: AsyncClient, api_prefix: str, token: str
) -> None:
    small = await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(request_payload={"log": "short"}),
        headers=_auth(token),
    )
    large = await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(request_payload={"log": "x" * 20_000}),
        headers=_auth(token),
    )

    assert small.json()["execution_plan"]["complexity"] == "SIMPLE"
    assert large.json()["execution_plan"]["complexity"] == "COMPLEX"


# ===========================================================================
# Refusals
# ===========================================================================
async def test_no_compatible_model_is_a_policy_conflict(
    client: AsyncClient, api_prefix: str, token: str
) -> None:
    """quality_check needs a vision model; only a reasoning model is registered."""
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(workload_type="quality_check"),
        headers=_auth(token),
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "no_compatible_model"
    assert set(body) == {"code", "message", "request_id", "details"}
