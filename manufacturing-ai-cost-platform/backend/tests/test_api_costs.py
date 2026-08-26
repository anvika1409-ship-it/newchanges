"""Cost endpoint tests.

Drives the real application with authentication and authorization active.

Two things are under test at once: that the responses match the contract's
CostSummary / CostBreakdown / CostTrend shapes, and that the authorization scope
actually reaches the SQL — a plant manager's totals must not include another
plant's spend even though every endpoint shares one query builder.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.base import Base
from app.db.models.telemetry import CostEvent, UsageEvent
from app.main import create_app
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
PLANT_1 = "plant-1"
PLANT_2 = "plant-2"
DEPT_1 = "dept-1"
MODEL_1 = "model-1"
AGENT_1 = "agent-1"

JAN_10 = datetime(2026, 1, 10, 9, 0)
JAN_11 = datetime(2026, 1, 11, 9, 0)


@pytest.fixture
def adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="admin-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )


@pytest.fixture
def plant_manager_token(adapter: DevelopmentIdentityAdapter) -> str:
    """Scoped to plant-1 only. The SECURITY.md section 4 example."""
    return adapter.issue_token(
        subject="pm-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),),
    )


@pytest.fixture
def tenant_b_admin_token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="admin-b",
        tenant_id=TENANT_B,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_B),),
    )


def _events(session: object) -> None:
    """Seed a fixed spread of spend.

    tenant-a / plant-1  Jan 10  actual 1.00
    tenant-a / plant-1  Jan 11  estimated 4.00
    tenant-a / plant-2  Jan 10  actual 8.00
    tenant-b / plant-1  Jan 10  actual 999.00   <- must never surface
    """
    rows = [
        (TENANT_A, PLANT_1, 1.00, None, "ACTUAL", JAN_10),
        (TENANT_A, PLANT_1, None, 4.00, "ESTIMATED", JAN_11),
        (TENANT_A, PLANT_2, 8.00, None, "ACTUAL", JAN_10),
        (TENANT_B, PLANT_1, 999.00, None, "ACTUAL", JAN_10),
    ]
    for tenant_id, plant_id, actual, estimated, provenance, timestamp in rows:
        usage_id = str(uuid.uuid4())
        session.add(  # type: ignore[attr-defined]
            UsageEvent(
                id=usage_id,
                request_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                plant_id=plant_id,
                department_id=DEPT_1,
                agent_id=AGENT_1,
                model_id=MODEL_1,
                timestamp=timestamp,
                total_tokens=100,
                status="SUCCESS",
                created_at=timestamp,
            )
        )
        session.add(  # type: ignore[attr-defined]
            CostEvent(
                id=str(uuid.uuid4()),
                usage_event_id=usage_id,
                actual_cost=actual,
                estimated_cost=estimated,
                currency="USD",
                provenance=provenance,
                created_at=timestamp,
            )
        )


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """The real app over an in-memory database seeded with known spend."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with app.state.database.session() as session:
            _events(session)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


# ===========================================================================
# Authorization
# ===========================================================================
@pytest.mark.parametrize(
    "path",
    ["/cost/summary", "/cost/by-model", "/cost/by-agent", "/cost/by-plant", "/cost/trend"],
)
async def test_cost_endpoints_require_authentication(
    client: AsyncClient, api_prefix: str, path: str
) -> None:
    response = await client.get(f"{api_prefix}{path}")
    assert response.status_code == 401


async def test_a_token_with_no_roles_is_forbidden(
    client: AsyncClient, api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(subject="nobody", tenant_id=TENANT_A, assignments=())
    response = await client.get(f"{api_prefix}/cost/summary", headers=_auth(token))
    assert response.status_code == 403


async def test_a_viewer_may_read_costs(
    client: AsyncClient, api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(
        subject="viewer-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A),),
    )
    response = await client.get(f"{api_prefix}/cost/summary", headers=_auth(token))
    assert response.status_code == 200


# ===========================================================================
# Tenant isolation
# ===========================================================================
async def test_another_tenants_spend_never_surfaces(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """Tenant B's 999.00 must not appear in tenant A's summary."""
    response = await client.get(f"{api_prefix}/cost/summary", headers=_auth(admin_token))

    assert response.status_code == 200
    assert response.json()["actual_cost"] == pytest.approx(9.00)  # 1 + 8


async def test_each_tenant_sees_only_its_own_spend(
    client: AsyncClient, api_prefix: str, tenant_b_admin_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/cost/summary", headers=_auth(tenant_b_admin_token)
    )
    assert response.json()["actual_cost"] == pytest.approx(999.00)


# ===========================================================================
# Plant scope
# ===========================================================================
async def test_a_plant_manager_sees_only_their_plant(
    client: AsyncClient, api_prefix: str, plant_manager_token: str
) -> None:
    """Plant-2's 8.00 is in the same tenant, and still must not appear."""
    response = await client.get(
        f"{api_prefix}/cost/summary", headers=_auth(plant_manager_token)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["actual_cost"] == pytest.approx(1.00)
    assert body["estimated_cost"] == pytest.approx(4.00)


async def test_a_plant_filter_outside_scope_is_refused(
    client: AsyncClient, api_prefix: str, plant_manager_token: str
) -> None:
    """Refused, not silently ignored — ignoring it would answer a probe for
    plant-2 with plant-1's data, which reads as success."""
    response = await client.get(
        f"{api_prefix}/cost/summary",
        params={"plant_id": PLANT_2},
        headers=_auth(plant_manager_token),
    )
    assert response.status_code == 403


async def test_an_admin_may_filter_to_any_plant(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/cost/summary",
        params={"plant_id": PLANT_2},
        headers=_auth(admin_token),
    )
    assert response.json()["actual_cost"] == pytest.approx(8.00)


async def test_by_plant_shows_one_row_for_a_plant_scoped_caller(
    client: AsyncClient, api_prefix: str, plant_manager_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/cost/by-plant", headers=_auth(plant_manager_token)
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == PLANT_1


# ===========================================================================
# Contract shapes
# ===========================================================================
async def test_summary_reports_actual_and_estimated_separately(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """The two are never blended into one unlabelled figure."""
    body = (
        await client.get(f"{api_prefix}/cost/summary", headers=_auth(admin_token))
    ).json()

    assert body["actual_cost"] == pytest.approx(9.00)
    assert body["estimated_cost"] == pytest.approx(4.00)
    assert body["currency"] == "USD"
    assert body["total_requests"] == 3
    assert body["unavailable_cost_events"] == 0


async def test_summary_leaves_the_forecast_null(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """Forecasting belongs to the intelligence layer. A straight-line guess
    returned in this field would be read as the platform's forecast."""
    body = (
        await client.get(f"{api_prefix}/cost/summary", headers=_auth(admin_token))
    ).json()
    assert body["forecast_month_end_cost"] is None


async def test_breakdown_by_model_shape(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    body = (
        await client.get(f"{api_prefix}/cost/by-model", headers=_auth(admin_token))
    ).json()

    assert body["dimension"] == "model"
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == MODEL_1
    assert item["actual_cost"] == pytest.approx(9.00)
    assert item["estimated_cost"] == pytest.approx(4.00)


async def test_breakdown_by_agent_shape(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    body = (
        await client.get(f"{api_prefix}/cost/by-agent", headers=_auth(admin_token))
    ).json()
    assert body["dimension"] == "agent"
    assert body["items"][0]["id"] == AGENT_1


async def test_trend_shape_and_ordering(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    body = (
        await client.get(
            f"{api_prefix}/cost/trend",
            params={"granularity": "day"},
            headers=_auth(admin_token),
        )
    ).json()

    assert body["granularity"] == "day"
    starts = [point["bucket_start"] for point in body["points"]]
    assert starts == sorted(starts)
    assert starts == ["2026-01-10T00:00:00", "2026-01-11T00:00:00"]


async def test_trend_defaults_to_daily(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    body = (
        await client.get(f"{api_prefix}/cost/trend", headers=_auth(admin_token))
    ).json()
    assert body["granularity"] == "day"


async def test_an_invalid_granularity_is_rejected(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/cost/trend",
        params={"granularity": "fortnight"},
        headers=_auth(admin_token),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# ===========================================================================
# Date range
# ===========================================================================
async def test_the_from_and_to_parameters_bound_the_result(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """The contract spells these `from` and `to`; `from` is a Python keyword,
    so the alias has to survive the round trip."""
    body = (
        await client.get(
            f"{api_prefix}/cost/summary",
            params={"from": "2026-01-10T00:00:00", "to": "2026-01-10T23:59:59"},
            headers=_auth(admin_token),
        )
    ).json()

    assert body["actual_cost"] == pytest.approx(9.00)
    assert body["estimated_cost"] == 0.0


async def test_a_range_with_no_events_is_empty_not_an_error(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    body = (
        await client.get(
            f"{api_prefix}/cost/summary",
            params={"from": "2030-01-01T00:00:00", "to": "2030-12-31T00:00:00"},
            headers=_auth(admin_token),
        )
    ).json()

    assert body["actual_cost"] == 0.0
    assert body["total_requests"] == 0
    assert body["average_cost_per_request"] is None


# ===========================================================================
# Acceptance: determinism
# ===========================================================================
async def test_repeated_requests_return_identical_bodies(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    first = await client.get(f"{api_prefix}/cost/by-model", headers=_auth(admin_token))
    second = await client.get(f"{api_prefix}/cost/by-model", headers=_auth(admin_token))
    assert first.json() == second.json()
