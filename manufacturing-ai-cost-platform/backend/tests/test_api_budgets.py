"""Budget endpoint tests.

Drives the real application with authentication and authorization active.

The authorization cases here are the SECURITY.md section 4 worked example made
concrete: a plant manager must not read, create or update another plant's
budget. Each of those is a separate control and each is asserted separately —
being able to create a budget you cannot read would be just as broken.
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
from app.db.models.control_plane import Plant, Tenant
from app.db.models.governance import Budget
from app.db.models.telemetry import CostEvent, UsageEvent
from app.main import create_app
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
PLANT_1 = "plant-1"
PLANT_2 = "plant-2"

#: Budget ids are fixed so tests can address them without a lookup round trip.
TENANT_BUDGET = "budget-tenant-a"
PLANT_1_BUDGET = "budget-plant-1"
PLANT_2_BUDGET = "budget-plant-2"
TENANT_B_BUDGET = "budget-tenant-b"

NOW = datetime(2026, 1, 10, 9, 0)


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
def finops_token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="finops-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.FINOPS_MANAGER, ScopeType.TENANT, TENANT_A),),
    )


@pytest.fixture
def viewer_token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="viewer-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A),),
    )


@pytest.fixture
def plant_1_manager_token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="pm-1",
        tenant_id=TENANT_A,
        assignments=(
            RoleAssignment(Role.FINOPS_MANAGER, ScopeType.PLANT, PLANT_1),
        ),
    )


@pytest.fixture
def tenant_b_admin_token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="admin-b",
        tenant_id=TENANT_B,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_B),),
    )


def _seed(session: object) -> None:
    """Two tenants, two plants, four budgets, and 90.00 of spend on plant-1."""
    for tenant_id in (TENANT_A, TENANT_B):
        session.add(  # type: ignore[attr-defined]
            Tenant(
                id=tenant_id,
                name=tenant_id,
                status="ACTIVE",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    for plant_id in (PLANT_1, PLANT_2):
        session.add(  # type: ignore[attr-defined]
            Plant(
                id=plant_id,
                tenant_id=TENANT_A,
                name=plant_id,
                location="X",
                timezone="UTC",
                status="ACTIVE",
                created_at=NOW,
                updated_at=NOW,
            )
        )

    budgets = [
        (TENANT_BUDGET, TENANT_A, "TENANT", TENANT_A, 10_000.0),
        (PLANT_1_BUDGET, TENANT_A, "PLANT", PLANT_1, 100.0),
        (PLANT_2_BUDGET, TENANT_A, "PLANT", PLANT_2, 100.0),
        (TENANT_B_BUDGET, TENANT_B, "TENANT", TENANT_B, 100.0),
    ]
    for budget_id, tenant_id, scope_type, scope_id, amount in budgets:
        session.add(  # type: ignore[attr-defined]
            Budget(
                id=budget_id,
                tenant_id=tenant_id,
                scope_type=scope_type,
                scope_id=scope_id,
                amount=amount,
                currency="USD",
                period="MONTHLY",
                warning_threshold_percent=80.0,
                critical_threshold_percent=95.0,
                status="ACTIVE",
                created_at=NOW,
                updated_at=NOW,
            )
        )

    # 90.00 spent on plant-1 in the current period: 90% of its 100.00 budget.
    usage_id = str(uuid.uuid4())
    session.add(  # type: ignore[attr-defined]
        UsageEvent(
            id=usage_id,
            request_id=str(uuid.uuid4()),
            tenant_id=TENANT_A,
            plant_id=PLANT_1,
            timestamp=datetime.now().replace(microsecond=0),
            total_tokens=100,
            status="SUCCESS",
            created_at=NOW,
        )
    )
    session.add(  # type: ignore[attr-defined]
        CostEvent(
            id=str(uuid.uuid4()),
            usage_event_id=usage_id,
            actual_cost=90.0,
            currency="USD",
            provenance="ACTUAL",
            created_at=NOW,
        )
    )


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.database.session() as session:
            _seed(session)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


# ===========================================================================
# Authorization
# ===========================================================================
@pytest.mark.parametrize("path", ["/budgets", "/budgets/status"])
async def test_budget_reads_require_authentication(
    client: AsyncClient, api_prefix: str, path: str
) -> None:
    assert (await client.get(f"{api_prefix}{path}")).status_code == 401


async def test_creating_a_budget_requires_authentication(
    client: AsyncClient, api_prefix: str
) -> None:
    response = await client.post(f"{api_prefix}/budgets", json={})
    assert response.status_code == 401


async def test_a_viewer_may_read_but_not_create(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    """VIEWER holds BUDGET_READ but not BUDGET_MANAGE."""
    assert (
        await client.get(f"{api_prefix}/budgets", headers=_auth(viewer_token))
    ).status_code == 200

    response = await client.post(
        f"{api_prefix}/budgets",
        json={
            "scope_type": "PLANT",
            "scope_id": PLANT_1,
            "amount": 50.0,
            "currency": "USD",
            "period": "MONTHLY",
        },
        headers=_auth(viewer_token),
    )
    assert response.status_code == 403


async def test_a_finops_manager_may_create(
    client: AsyncClient, api_prefix: str, finops_token: str
) -> None:
    response = await client.post(
        f"{api_prefix}/budgets",
        json={
            "scope_type": "WORKLOAD",
            "scope_id": "workload-9",
            "amount": 250.0,
            "currency": "USD",
            "period": "DAILY",
        },
        headers=_auth(finops_token),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["scope_type"] == "WORKLOAD"
    assert body["amount"] == pytest.approx(250.0)
    assert body["status"] == "ACTIVE"


# ===========================================================================
# Tenant isolation
# ===========================================================================
async def test_a_tenants_budget_list_excludes_other_tenants(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    body = (
        await client.get(f"{api_prefix}/budgets", headers=_auth(admin_token))
    ).json()

    ids = {item["id"] for item in body["items"]}
    assert TENANT_B_BUDGET not in ids
    assert ids == {TENANT_BUDGET, PLANT_1_BUDGET, PLANT_2_BUDGET}


async def test_updating_another_tenants_budget_is_a_404(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """404, not 403 — a 403 would confirm the row exists across the boundary."""
    response = await client.patch(
        f"{api_prefix}/budgets/{TENANT_B_BUDGET}",
        json={"amount": 1.0},
        headers=_auth(admin_token),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_a_created_budget_belongs_to_the_callers_tenant(
    client: AsyncClient, api_prefix: str, tenant_b_admin_token: str
) -> None:
    """tenant_id is never accepted from the request body."""
    await client.post(
        f"{api_prefix}/budgets",
        json={
            "scope_type": "MODEL",
            "scope_id": "model-x",
            "amount": 10.0,
            "currency": "USD",
            "period": "MONTHLY",
        },
        headers=_auth(tenant_b_admin_token),
    )
    body = (
        await client.get(f"{api_prefix}/budgets", headers=_auth(tenant_b_admin_token))
    ).json()
    scopes = {item["scope_id"] for item in body["items"]}
    assert "model-x" in scopes


async def test_a_client_supplied_tenant_id_is_rejected(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """The schema forbids extra fields, so a smuggled tenant_id is a 422 rather
    than being quietly ignored."""
    response = await client.post(
        f"{api_prefix}/budgets",
        json={
            "scope_type": "MODEL",
            "scope_id": "model-y",
            "amount": 10.0,
            "currency": "USD",
            "period": "MONTHLY",
            "tenant_id": TENANT_B,
        },
        headers=_auth(admin_token),
    )
    assert response.status_code == 422


# ===========================================================================
# Plant scope — the SECURITY.md section 4 example
# ===========================================================================
async def test_a_plant_scoped_manager_sees_only_their_plants_budget(
    client: AsyncClient, api_prefix: str, plant_1_manager_token: str
) -> None:
    body = (
        await client.get(f"{api_prefix}/budgets", headers=_auth(plant_1_manager_token))
    ).json()

    ids = {item["id"] for item in body["items"]}
    assert ids == {PLANT_1_BUDGET}
    assert PLANT_2_BUDGET not in ids
    # The tenant-wide budget is out of reach too: plant scope does not cover it.
    assert TENANT_BUDGET not in ids


async def test_a_plant_scoped_manager_cannot_update_another_plants_budget(
    client: AsyncClient, api_prefix: str, plant_1_manager_token: str
) -> None:
    response = await client.patch(
        f"{api_prefix}/budgets/{PLANT_2_BUDGET}",
        json={"amount": 1.0},
        headers=_auth(plant_1_manager_token),
    )
    assert response.status_code == 403


async def test_a_plant_scoped_manager_can_update_their_own(
    client: AsyncClient, api_prefix: str, plant_1_manager_token: str
) -> None:
    response = await client.patch(
        f"{api_prefix}/budgets/{PLANT_1_BUDGET}",
        json={"amount": 500.0, "reason": "expanded line"},
        headers=_auth(plant_1_manager_token),
    )
    assert response.status_code == 200
    assert response.json()["amount"] == pytest.approx(500.0)


async def test_a_plant_scoped_manager_cannot_create_for_another_plant(
    client: AsyncClient, api_prefix: str, plant_1_manager_token: str
) -> None:
    """Authorized against the *proposed* scope, before any row exists.

    Without this check a plant manager could create a budget over another plant
    and then legitimately read it back.
    """
    response = await client.post(
        f"{api_prefix}/budgets",
        json={
            "scope_type": "PLANT",
            "scope_id": PLANT_2,
            "amount": 50.0,
            "currency": "USD",
            "period": "MONTHLY",
        },
        headers=_auth(plant_1_manager_token),
    )
    assert response.status_code == 403


# ===========================================================================
# Validation
# ===========================================================================
async def test_a_duplicate_scope_is_refused(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """Two active budgets at one scope would make the decision depend on which
    row the database returned first."""
    response = await client.post(
        f"{api_prefix}/budgets",
        json={
            "scope_type": "PLANT",
            "scope_id": PLANT_1,
            "amount": 50.0,
            "currency": "USD",
            "period": "MONTHLY",
        },
        headers=_auth(admin_token),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"


async def test_a_non_positive_amount_is_refused(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """A zero-amount budget would block every request at its scope."""
    response = await client.post(
        f"{api_prefix}/budgets",
        json={
            "scope_type": "MODEL",
            "scope_id": "model-z",
            "amount": 0.0,
            "currency": "USD",
            "period": "MONTHLY",
        },
        headers=_auth(admin_token),
    )
    assert response.status_code == 422


async def test_an_unknown_scope_type_is_refused(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """REQUEST is not a stored budget scope (DATABASE_SCHEMA.md section 12)."""
    response = await client.post(
        f"{api_prefix}/budgets",
        json={
            "scope_type": "REQUEST",
            "scope_id": "req-1",
            "amount": 5.0,
            "currency": "USD",
            "period": "MONTHLY",
        },
        headers=_auth(admin_token),
    )
    assert response.status_code == 422


async def test_currency_is_required(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """The contract lists it under `required`, which wins over its stated
    default — and a budget in a currency the platform does not aggregate in
    would be reported unevaluable rather than enforced."""
    response = await client.post(
        f"{api_prefix}/budgets",
        json={
            "scope_type": "MODEL",
            "scope_id": "model-w",
            "amount": 5.0,
            "period": "MONTHLY",
        },
        headers=_auth(admin_token),
    )
    assert response.status_code == 422


async def test_inverted_thresholds_are_refused(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """A warning above the critical level would never fire in the right order."""
    response = await client.patch(
        f"{api_prefix}/budgets/{PLANT_1_BUDGET}",
        json={"warning_threshold_percent": 99.0, "critical_threshold_percent": 50.0},
        headers=_auth(admin_token),
    )
    assert response.status_code == 400


async def test_a_partial_update_leaves_other_fields_alone(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    body = (
        await client.patch(
            f"{api_prefix}/budgets/{PLANT_1_BUDGET}",
            json={"amount": 777.0},
            headers=_auth(admin_token),
        )
    ).json()

    assert body["amount"] == pytest.approx(777.0)
    assert body["period"] == "MONTHLY"
    assert body["warning_threshold_percent"] == pytest.approx(80.0)


# ===========================================================================
# Status
# ===========================================================================
async def test_status_reports_the_threshold_state(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """90.00 spent against plant-1's 100.00 budget is 90% — WARNING."""
    body = (
        await client.get(f"{api_prefix}/budgets/status", headers=_auth(admin_token))
    ).json()

    by_id = {item["budget_id"]: item for item in body["items"]}
    plant_1 = by_id[PLANT_1_BUDGET]

    assert plant_1["consumed_actual_cost"] == pytest.approx(90.0)
    assert plant_1["consumed_percent"] == pytest.approx(90.0)
    assert plant_1["threshold_state"] == "WARNING"


async def test_status_reports_normal_for_an_untouched_budget(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """NORMAL, not OK — the contract enum was updated to carry all four states."""
    body = (
        await client.get(f"{api_prefix}/budgets/status", headers=_auth(admin_token))
    ).json()

    by_id = {item["budget_id"]: item for item in body["items"]}
    assert by_id[PLANT_2_BUDGET]["threshold_state"] == "NORMAL"


async def test_status_reports_exceeded(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    """Lower the budget under the recorded spend and the state must say so.

    Reporting an exceeded budget as merely CRITICAL is exactly what the old
    three-value enum forced, and why it was changed.
    """
    await client.patch(
        f"{api_prefix}/budgets/{PLANT_1_BUDGET}",
        json={"amount": 50.0},
        headers=_auth(admin_token),
    )
    body = (
        await client.get(f"{api_prefix}/budgets/status", headers=_auth(admin_token))
    ).json()

    by_id = {item["budget_id"]: item for item in body["items"]}
    assert by_id[PLANT_1_BUDGET]["threshold_state"] == "EXCEEDED"


async def test_status_reports_critical(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    await client.patch(
        f"{api_prefix}/budgets/{PLANT_1_BUDGET}",
        json={"amount": 92.0},
        headers=_auth(admin_token),
    )
    body = (
        await client.get(f"{api_prefix}/budgets/status", headers=_auth(admin_token))
    ).json()

    by_id = {item["budget_id"]: item for item in body["items"]}
    assert by_id[PLANT_1_BUDGET]["threshold_state"] == "CRITICAL"


async def test_status_respects_plant_scope(
    client: AsyncClient, api_prefix: str, plant_1_manager_token: str
) -> None:
    body = (
        await client.get(
            f"{api_prefix}/budgets/status", headers=_auth(plant_1_manager_token)
        )
    ).json()

    assert {item["budget_id"] for item in body["items"]} == {PLANT_1_BUDGET}


# ===========================================================================
# Acceptance: determinism
# ===========================================================================
async def test_repeated_status_requests_are_identical(
    client: AsyncClient, api_prefix: str, admin_token: str
) -> None:
    first = await client.get(f"{api_prefix}/budgets/status", headers=_auth(admin_token))
    second = await client.get(f"{api_prefix}/budgets/status", headers=_auth(admin_token))
    assert first.json() == second.json()
