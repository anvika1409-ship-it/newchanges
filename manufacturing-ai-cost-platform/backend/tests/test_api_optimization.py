"""API integration tests for optimization endpoints (STEP 15).

Tests cover:
- POST /api/v1/optimization/analyze (202 Accepted, recommendation_id returned, DB persistence)
- GET /api/v1/optimization/recommendations (list retrieval, status filtering, pagination)
- Data provenance check (ESTIMATED/SIMULATED)
- Governance rule: no autonomous activation
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.base import Base
from app.db.models.optimization import (
    OptimizationRecommendationRecord,
    OptimizationRiskLevel,
    OptimizationStatus,
)
from app.main import create_app
from app.repositories.optimization_repository import OptimizationRepository
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType

TENANT_1 = "tenant-1"


@pytest.fixture
def identity_adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest.fixture
def auth_headers(identity_adapter: DevelopmentIdentityAdapter) -> dict[str, str]:
    token = identity_adapter.issue_token(
        subject="optimizer-1",
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
        yield app


@pytest_asyncio.fixture
async def client(app_instance) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


# ── Helpers ───────────────────────────────────────────────────────


async def _seed_recommendations(session) -> list[OptimizationRecommendationRecord]:
    repo = OptimizationRepository(session)
    records = [
        OptimizationRecommendationRecord(
            id="rec-001",
            tenant_id=TENANT_1,
            workload_id="predictive_maintenance",
            current_strategy="STATIC_PRIMARY (claude-3-5-sonnet)",
            recommended_strategy="Model Routing: Tiered Mini Routing",
            estimated_saving=450.0,
            estimated_saving_percent=45.0,
            quality_impact_percent=0.5,
            latency_impact_percent=-35.0,
            risk_level=OptimizationRiskLevel.LOW,
            recommendation_reason="Route standard queries to gpt-4o-mini",
            status=OptimizationStatus.PENDING_APPROVAL,
            created_at=datetime.now(UTC),
        ),
        OptimizationRecommendationRecord(
            id="rec-002",
            tenant_id=TENANT_1,
            workload_id="vision_inspection",
            current_strategy="FULL_CONTEXT",
            recommended_strategy="Context Reduction: Sliding Window",
            estimated_saving=250.0,
            estimated_saving_percent=25.0,
            quality_impact_percent=0.2,
            latency_impact_percent=-20.0,
            risk_level=OptimizationRiskLevel.LOW,
            recommendation_reason="Trim historical prompt tokens",
            status=OptimizationStatus.APPROVED,
            created_at=datetime.now(UTC),
        ),
    ]
    return await repo.create_many(records)


# ── API Tests ─────────────────────────────────────────────────────


class TestOptimizationAPI:
    """Tests for GET and POST /api/v1/optimization endpoints."""

    async def test_analyze_endpoint_returns_202_and_persists(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        """POST /api/v1/optimization/analyze returns 202 with recommendation_id and persists to DB."""
        payload = {
            "workload_id": "predictive_maintenance",
            "simulation_only": True,
            "target_saving_percent": 30.0,
        }

        response = await client.post(
            "/api/v1/optimization/analyze", json=payload, headers=auth_headers
        )
        assert response.status_code == 202

        data = response.json()
        assert "request_id" in data
        assert "recommendation_id" in data
        assert data["status"] in ("PENDING_APPROVAL", "DRAFT")

        # Verify persisted in database
        async with app_instance.state.database.session() as session:
            repo = OptimizationRepository(session)
            persisted = await repo.get_by_id(data["recommendation_id"])
            assert persisted is not None
            assert persisted.workload_id == "predictive_maintenance"
            assert persisted.estimated_saving > 0.0
            assert persisted.status == OptimizationStatus.PENDING_APPROVAL

    async def test_list_recommendations_endpoint(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        """GET /api/v1/optimization/recommendations returns recommendation list."""
        async with app_instance.state.database.session() as session:
            await _seed_recommendations(session)

        response = await client.get("/api/v1/optimization/recommendations", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert "items" in data
        assert "page" in data
        assert len(data["items"]) >= 2

        # Check item schema
        item = data["items"][0]
        assert "id" in item
        assert "workload_id" in item
        assert "recommended_strategy" in item
        assert "estimated_saving" in item
        assert item["provenance"] in ("ESTIMATED", "SIMULATED")

    async def test_filter_recommendations_by_status(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        """GET /api/v1/optimization/recommendations?status=APPROVED filters accurately."""
        async with app_instance.state.database.session() as session:
            await _seed_recommendations(session)

        response = await client.get(
            "/api/v1/optimization/recommendations?status=APPROVED", headers=auth_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["status"] == "APPROVED"
        assert data["items"][0]["id"] == "rec-002"

    async def test_pagination_on_recommendations(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        """Pagination limit and offset behave properly."""
        async with app_instance.state.database.session() as session:
            await _seed_recommendations(session)

        response = await client.get(
            "/api/v1/optimization/recommendations?limit=1&offset=0", headers=auth_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1
        assert data["page"]["limit"] == 1
        assert data["page"]["offset"] == 0
        assert data["page"]["total"] >= 2
