"""Integration tests for Cost API endpoints.

Tests cover:
- GET /cost/summary
- GET /cost/by-model
- GET /cost/by-agent
- GET /cost/by-plant
- GET /cost/trend
- Query parameters (plant_id, department_id, from, to, granularity)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.base import Base
from app.db.models.control_plane import Department, Plant, Tenant, Workload
from app.db.models.telemetry import CostEvent, UsageEvent
from app.main import create_app
from app.repositories.telemetry_repository import CostEventRepository, UsageEventRepository
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType

TENANT_1 = "tenant-1"
PLANT_1 = "plant-1"
DEPT_1 = "dept-1"


@pytest.fixture
def identity_adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest.fixture
def auth_headers(identity_adapter: DevelopmentIdentityAdapter) -> dict[str, str]:
    token = identity_adapter.issue_token(
        subject="finops-user",
        tenant_id=TENANT_1,
        assignments=(RoleAssignment(Role.FINOPS_MANAGER, ScopeType.TENANT, TENANT_1),),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def app_instance(settings: Settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.database.session() as session:
            t = Tenant(id=TENANT_1, name="Tenant 1", status="ACTIVE")
            p = Plant(id=PLANT_1, tenant_id=TENANT_1, name="Plant 1", status="ACTIVE")
            d = Department(id=DEPT_1, plant_id=PLANT_1, name="Dept 1", status="ACTIVE")
            wl = Workload(
                id="predictive_maintenance",
                plant_id=PLANT_1,
                department_id=DEPT_1,
                name="PM",
                workload_type="predictive_maintenance",
                status="ACTIVE",
            )
            session.add_all([t, p, d, wl])
            await session.commit()
        yield app


@pytest_asyncio.fixture
async def client(app_instance) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def _seed_test_telemetry(session) -> None:
    usage_repo = UsageEventRepository(session)
    cost_repo = CostEventRepository(session)
    now = datetime.now(UTC)

    events = [
        # Event 1: Model A, Agent X, Actual cost
        (
            UsageEvent(
                id=str(uuid.uuid4()),
                request_id="req-001",
                tenant_id=TENANT_1,
                plant_id=PLANT_1,
                department_id=DEPT_1,
                workload_id="predictive_maintenance",
                agent_id="agent-x",
                model_id="model-a",
                timestamp=now - timedelta(hours=2),
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                execution_time_ms=100,
                status="SUCCESS",
            ),
            CostEvent(
                id=str(uuid.uuid4()),
                actual_cost=0.05,
                estimated_cost=0.05,
                currency="USD",
                provenance="ACTUAL",
            ),
        ),
        # Event 2: Model B, Agent Y, Estimated cost
        (
            UsageEvent(
                id=str(uuid.uuid4()),
                request_id="req-002",
                tenant_id=TENANT_1,
                plant_id=PLANT_1,
                department_id=DEPT_1,
                workload_id="predictive_maintenance",
                agent_id="agent-y",
                model_id="model-b",
                timestamp=now - timedelta(hours=1),
                input_tokens=200,
                output_tokens=100,
                total_tokens=300,
                execution_time_ms=150,
                status="SUCCESS",
            ),
            CostEvent(
                id=str(uuid.uuid4()),
                actual_cost=None,
                estimated_cost=0.08,
                currency="USD",
                provenance="ESTIMATED",
            ),
        ),
    ]

    for ue, ce in events:
        ce.usage_event_id = ue.id
        await usage_repo.add(ue)
        await cost_repo.add(ce)


class TestCostAPI:
    """Integration tests for GET /api/v1/cost endpoints."""

    async def test_get_cost_summary(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_test_telemetry(session)

        response = await client.get("/api/v1/cost/summary", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total_requests"] == 2
        assert data["total_tokens"] == 450
        assert data["actual_cost"] == 0.05
        assert data["estimated_cost"] == 0.08
        assert data["currency"] == "USD"

    async def test_get_cost_by_model(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_test_telemetry(session)

        response = await client.get("/api/v1/cost/by-model", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["dimension"] == "model"
        assert len(data["items"]) == 2
        model_names = {item["id"] for item in data["items"]}
        assert "model-a" in model_names
        assert "model-b" in model_names

    async def test_get_cost_by_agent(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_test_telemetry(session)

        response = await client.get("/api/v1/cost/by-agent", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["dimension"] == "agent"
        assert len(data["items"]) == 2
        agent_names = {item["id"] for item in data["items"]}
        assert "agent-x" in agent_names
        assert "agent-y" in agent_names

    async def test_get_cost_by_plant(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_test_telemetry(session)

        response = await client.get("/api/v1/cost/by-plant", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["dimension"] == "plant"
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == PLANT_1

    async def test_get_cost_trend(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_test_telemetry(session)

        response = await client.get(
            "/api/v1/cost/trend?granularity=hour", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["granularity"] == "hour"
        assert len(data["points"]) > 0
