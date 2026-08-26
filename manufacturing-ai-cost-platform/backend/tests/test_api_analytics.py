"""Integration and API tests for cost forecasting, anomaly detection, and worker persistence.

Tests cover:
- GET /api/v1/forecasts endpoint
- GET /api/v1/anomalies endpoint
- Horizon filtering and scope filtering
- Severity filtering
- CostAnalyticsWorker background execution and persistence
- Provenance FORECAST verification
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.base import Base
from app.db.models.analytics import AnomalyRecord, AnomalySeverity, ForecastRecord
from app.main import create_app
from app.repositories.anomaly_repository import AnomalyRepository
from app.repositories.forecast_repository import ForecastRepository
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType
from app.services.analytics_worker import CostAnalyticsWorker

TENANT_1 = "tenant-1"


@pytest.fixture
def identity_adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest.fixture
def auth_headers(identity_adapter: DevelopmentIdentityAdapter) -> dict[str, str]:
    token = identity_adapter.issue_token(
        subject="analyst-1",
        tenant_id=TENANT_1,
        assignments=(RoleAssignment(Role.ANALYST, ScopeType.TENANT, TENANT_1),),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def app_instance(settings: Settings):
    from app.db.models.control_plane import Tenant

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.database.session() as session:
            session.add(Tenant(id="tenant-1", name="Tenant 1", status="ACTIVE"))
            await session.commit()
        yield app


@pytest_asyncio.fixture
async def client(app_instance) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


# ── Fixtures & Seed Helpers ───────────────────────────────────────


async def _seed_forecasts(session) -> list[ForecastRecord]:
    repo = ForecastRepository(session)
    today = date.today()
    records = [
        ForecastRecord(
            tenant_id="tenant-1",
            scope_type="TENANT",
            scope_id="tenant-1",
            forecast_date=today + timedelta(days=i),
            predicted_cost=100.0 + i * 2.0,
            lower_bound=90.0 + i * 2.0,
            upper_bound=110.0 + i * 2.0,
            confidence=0.95,
            forecast_model_name="baseline_linear_runrate",
            forecast_model_version="1.0.0",
        )
        for i in range(1, 15)
    ]
    # Add a workload-scoped forecast
    records.append(
        ForecastRecord(
            tenant_id="tenant-1",
            scope_type="WORKLOAD",
            scope_id="pdm_agent",
            forecast_date=today + timedelta(days=1),
            predicted_cost=25.0,
            lower_bound=20.0,
            upper_bound=30.0,
            confidence=0.90,
            forecast_model_name="baseline_linear_runrate",
            forecast_model_version="1.0.0",
        )
    )
    return await repo.create_many(records)


async def _seed_anomalies(session) -> list[AnomalyRecord]:
    repo = AnomalyRepository(session)
    now = datetime.now(UTC)
    records = [
        AnomalyRecord(
            tenant_id="tenant-1",
            timestamp=now - timedelta(hours=2),
            scope_type="TENANT",
            scope_id="tenant-1",
            anomaly_type="cost_spike",
            severity=AnomalySeverity.HIGH,
            expected_value=50.0,
            actual_value=180.0,
            deviation_percent=260.0,
            reason="Cost spike detected on tenant",
            status="OPEN",
        ),
        AnomalyRecord(
            tenant_id="tenant-1",
            timestamp=now - timedelta(hours=1),
            scope_type="WORKLOAD",
            scope_id="supply_chain",
            anomaly_type="latency_spike",
            severity=AnomalySeverity.CRITICAL,
            expected_value=400.0,
            actual_value=1600.0,
            deviation_percent=300.0,
            reason="Critical latency surge on supply_chain",
            status="OPEN",
        ),
        AnomalyRecord(
            tenant_id="tenant-1",
            timestamp=now,
            scope_type="WORKLOAD",
            scope_id="pdm_agent",
            anomaly_type="token_spike",
            severity=AnomalySeverity.LOW,
            expected_value=5000.0,
            actual_value=6500.0,
            deviation_percent=30.0,
            reason="Minor token variance",
            status="RESOLVED",
        ),
    ]
    return await repo.create_many(records)


# ── Forecast API Tests ────────────────────────────────────────────


class TestForecastAPI:
    """Tests for GET /api/v1/forecasts."""

    async def test_get_forecasts(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_forecasts(session)

        response = await client.get("/api/v1/forecasts", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert "items" in data
        assert "page" in data
        assert len(data["items"]) >= 1

        # Check schema & provenance
        item = data["items"][0]
        assert item["provenance"] == "FORECAST"
        assert "predicted_cost" in item
        assert "lower_bound" in item
        assert "upper_bound" in item

    async def test_forecast_scope_filter(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_forecasts(session)

        response = await client.get(
            "/api/v1/forecasts?scope_type=WORKLOAD&scope_id=pdm_agent", headers=auth_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["scope_type"] == "WORKLOAD"
        assert data["items"][0]["scope_id"] == "pdm_agent"

    async def test_forecast_pagination(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_forecasts(session)

        response = await client.get("/api/v1/forecasts?limit=5&offset=0", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 5
        assert data["page"]["limit"] == 5
        assert data["page"]["offset"] == 0
        assert data["page"]["total"] >= 5


# ── Anomaly API Tests ─────────────────────────────────────────────


class TestAnomalyAPI:
    """Tests for GET /api/v1/anomalies."""

    async def test_get_anomalies(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_anomalies(session)

        response = await client.get("/api/v1/anomalies", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert "items" in data
        assert "page" in data
        assert len(data["items"]) >= 3

        item = data["items"][0]
        assert "anomaly_type" in item
        assert "severity" in item
        assert "expected_value" in item
        assert "actual_value" in item

    async def test_anomaly_severity_filter(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_anomalies(session)

        response = await client.get("/api/v1/anomalies?severity=CRITICAL", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["severity"] == "CRITICAL"
        assert data["items"][0]["anomaly_type"] == "latency_spike"

    async def test_anomaly_scope_filter(
        self, client: AsyncClient, app_instance, auth_headers: dict[str, str]
    ) -> None:
        async with app_instance.state.database.session() as session:
            await _seed_anomalies(session)

        response = await client.get(
            "/api/v1/anomalies?scope_type=WORKLOAD&scope_id=supply_chain", headers=auth_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["scope_id"] == "supply_chain"


# ── Background Worker Tests ──────────────────────────────────────


class TestCostAnalyticsWorker:
    """Tests for asynchronous background analytics worker execution."""

    async def test_worker_forecasting_job(self, app_instance) -> None:
        async with app_instance.state.database.session() as session:
            forecast_repo = ForecastRepository(session)
            worker = CostAnalyticsWorker(forecast_repository=forecast_repo)

            history = [(date.today() - timedelta(days=i), 50.0 + i) for i in range(10, 0, -1)]
            result = await worker.run_forecasting_job(
                historical_daily_costs=history,
                scope_type="TENANT",
                scope_id="tenant-1",
                tenant_id="tenant-1",
                horizon_days=7,
            )

            assert result.horizon_days == 7
            assert len(result.daily_points) == 7

            # Verify persisted in database
            records, count = await forecast_repo.list_forecasts(
                scope_type="TENANT", scope_id="tenant-1"
            )
            assert count == 7
            assert len(records) == 7

    async def test_worker_anomaly_detection_job(self, app_instance) -> None:
        async with app_instance.state.database.session() as session:
            anomaly_repo = AnomalyRepository(session)
            worker = CostAnalyticsWorker(anomaly_repository=anomaly_repo)

            metrics = {"cost_usd": 500.0, "latency_ms": 2500.0}
            baseline = {
                "cost_usd": {"mean": 50.0, "std": 10.0},
                "latency_ms": {"mean": 450.0, "std": 100.0},
            }

            anomalies = await worker.run_anomaly_detection_job(
                current_metrics=metrics,
                historical_baseline=baseline,
                scope_type="WORKLOAD",
                scope_id="vision_qc",
            )

            assert len(anomalies) == 2

            # Verify persisted in database
            records, count = await anomaly_repo.list_anomalies(
                scope_type="WORKLOAD", scope_id="vision_qc"
            )
            assert count == 2
            assert len(records) == 2
