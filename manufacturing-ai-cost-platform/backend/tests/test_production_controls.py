"""Regression tests for controls found inert during the readiness pass.

Each of these existed as code with unit tests and was invoked by nothing, so the
control was present and enforcing nothing. These tests assert the *wiring*, not
the mechanism — that is the part that was missing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models.audit import AuditEvent
from app.db.models.control_plane import Department, Plant, Tenant, Workload
from app.db.models.registry import ModelRegistryEntry
from app.main import create_app
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType

TENANT = "tenant-prod"
PLANT = "plant-prod"
DEPARTMENT = "dept-prod"
WORKLOAD = "wl-prod"


@pytest.fixture
def adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


def token_for(adapter: DevelopmentIdentityAdapter, role: Role, subject: str) -> str:
    return adapter.issue_token(
        subject=subject,
        tenant_id=TENANT,
        assignments=(RoleAssignment(role, ScopeType.TENANT, TENANT),),
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def platform(settings: Settings) -> AsyncIterator[tuple[Any, AsyncClient]]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.database.session() as session:
            session.add_all(
                [
                    Tenant(id=TENANT, name="Prod Tenant", status="ACTIVE"),
                    Plant(id=PLANT, tenant_id=TENANT, name="Prod Plant", status="ACTIVE"),
                    Department(
                        id=DEPARTMENT, plant_id=PLANT, name="Prod Dept", status="ACTIVE"
                    ),
                    Workload(
                        id=WORKLOAD,
                        plant_id=PLANT,
                        department_id=DEPARTMENT,
                        name="Spindle prediction",
                        workload_type="predictive_maintenance",
                        business_priority="NORMAL",
                        risk_level="MEDIUM",
                        status="ACTIVE",
                    ),
                ]
            )
            session.add(
                ModelRegistryEntry(
                    id=str(uuid.uuid4()),
                    model_name="prod/reasoning",
                    provider="genailab",
                    capability="reasoning",
                    enabled=True,
                )
            )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


def body() -> dict[str, Any]:
    return {
        "workload_type": "predictive_maintenance",
        "business_priority": "NORMAL",
        "request_payload": {"sensor": "nominal"},
    }


async def audit_events(app: Any) -> list[AuditEvent]:
    async with app.state.database.session() as session:
        rows = await session.execute(select(AuditEvent))
        return list(rows.scalars().all())


# ===========================================================================
# Rate limiting is wired, not just implemented
# ===========================================================================
def test_the_app_builds_a_rate_limiter(settings: Settings) -> None:
    """The limiter was implemented and tested while nothing constructed it."""
    from app.core.rate_limit import RateLimiter

    app = create_app(settings)
    assert isinstance(app.state.rate_limiter, RateLimiter)


async def test_the_expensive_endpoint_is_rate_limited(
    platform: tuple[Any, AsyncClient], api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """Exceeding the window returns 429 rather than spending more."""
    app, client = platform
    app.state.settings.ai_execute_rate_limit_requests = 3
    app.state.settings.ai_execute_rate_limit_window_seconds = 60.0

    token = token_for(adapter, Role.AI_ENGINEER, "rl-user")
    statuses = [
        (
            await client.post(
                f"{api_prefix}/ai/execute", json=body(), headers=auth(token)
            )
        ).status_code
        for _ in range(5)
    ]

    assert statuses[:3] == [200, 200, 200]
    assert statuses[3:] == [429, 429]


async def test_a_rate_limited_request_never_reaches_the_gateway(
    platform: tuple[Any, AsyncClient], api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """A refusal must cost nothing — that is the point of the limit."""
    app, client = platform
    app.state.settings.ai_execute_rate_limit_requests = 1
    token = token_for(adapter, Role.AI_ENGINEER, "rl-cost")

    await client.post(f"{api_prefix}/ai/execute", json=body(), headers=auth(token))
    calls_after_first = app.state.model_gateway.inner.call_count

    refused = await client.post(f"{api_prefix}/ai/execute", json=body(), headers=auth(token))

    assert refused.status_code == 429
    assert app.state.model_gateway.inner.call_count == calls_after_first


async def test_one_tenant_cannot_exhaust_another_s_allowance(
    platform: tuple[Any, AsyncClient], api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """Keyed on the principal, so a noisy caller does not lock out everyone."""
    app, client = platform
    app.state.settings.ai_execute_rate_limit_requests = 1

    first = token_for(adapter, Role.AI_ENGINEER, "caller-a")
    second = token_for(adapter, Role.AI_ENGINEER, "caller-b")

    await client.post(f"{api_prefix}/ai/execute", json=body(), headers=auth(first))
    exhausted = await client.post(f"{api_prefix}/ai/execute", json=body(), headers=auth(first))
    other = await client.post(f"{api_prefix}/ai/execute", json=body(), headers=auth(second))

    assert exhausted.status_code == 429
    assert other.status_code == 200


async def test_an_unauthorized_caller_cannot_consume_the_allowance(
    platform: tuple[Any, AsyncClient], api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """The permission guard runs first, so a VIEWER's rejected attempts do not
    burn the tenant's budget of requests."""
    app, client = platform
    app.state.settings.ai_execute_rate_limit_requests = 1
    viewer = token_for(adapter, Role.VIEWER, "viewer-1")
    engineer = token_for(adapter, Role.AI_ENGINEER, "engineer-1")

    for _ in range(3):
        refused = await client.post(
            f"{api_prefix}/ai/execute", json=body(), headers=auth(viewer)
        )
        assert refused.status_code == 403

    allowed = await client.post(
        f"{api_prefix}/ai/execute", json=body(), headers=auth(engineer)
    )
    assert allowed.status_code == 200


# ===========================================================================
# Audit logging actually writes
# ===========================================================================
async def test_an_approval_writes_an_audit_record(
    platform: tuple[Any, AsyncClient], api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """SECURITY.md section 16 names "optimization approved" as auditable.

    The table and repository existed; nothing wrote to them, so
    /governance/audit returned empty no matter what happened.
    """
    app, client = platform
    token = token_for(adapter, Role.ADMIN, "audit-admin")

    created = await client.post(
        f"{api_prefix}/optimization/analyze",
        json={"workload_id": WORKLOAD},
        headers=auth(token),
    )
    rec_id = created.json()["recommendation_id"]

    await client.post(
        f"{api_prefix}/optimization/{rec_id}/approve",
        json={"decision": "APPROVED", "comments": "reviewed"},
        headers=auth(token),
    )

    events = await audit_events(app)
    assert len(events) == 1
    event = events[0]
    assert event.action == "optimization.approved"
    assert event.resource_type == "optimization_recommendation"
    assert event.resource_id == rec_id
    assert event.user_id == "audit-admin"
    assert event.tenant_id == TENANT


async def test_a_rejection_is_audited_distinctly_from_an_approval(
    platform: tuple[Any, AsyncClient], api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """Both decisions are auditable, and must not be recorded as the same thing."""
    app, client = platform
    token = token_for(adapter, Role.ADMIN, "audit-admin")

    created = await client.post(
        f"{api_prefix}/optimization/analyze",
        json={"workload_id": WORKLOAD},
        headers=auth(token),
    )
    rec_id = created.json()["recommendation_id"]

    await client.post(
        f"{api_prefix}/optimization/{rec_id}/approve",
        json={"decision": "REJECTED", "comments": "quality drop too large"},
        headers=auth(token),
    )

    events = await audit_events(app)
    assert len(events) == 1
    assert events[0].action == "optimization.rejected"
    assert events[0].reason == "quality drop too large"


async def test_the_audit_record_carries_request_correlation(
    platform: tuple[Any, AsyncClient], api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    """An audit entry must join to the telemetry for the same request."""
    app, client = platform
    token = token_for(adapter, Role.ADMIN, "audit-admin")

    created = await client.post(
        f"{api_prefix}/optimization/analyze",
        json={"workload_id": WORKLOAD},
        headers=auth(token),
    )
    rec_id = created.json()["recommendation_id"]

    await client.post(
        f"{api_prefix}/optimization/{rec_id}/approve",
        json={"decision": "APPROVED"},
        headers={**auth(token), "X-Request-ID": "audit-corr-1"},
    )

    events = await audit_events(app)
    assert events[0].request_id == "audit-corr-1"
    assert events[0].trace_id is not None


def test_audit_state_snapshots_are_redacted_not_dropped() -> None:
    """A record containing a secret is redacted, not discarded.

    Dropping it would destroy the evidence that a secret was present
    (SECURITY.md section 16).
    """
    from app.services.audit import _safe_state

    clean = _safe_state({"status": "APPROVED"})
    assert clean is not None and "APPROVED" in clean

    leaked = _safe_state({"token": "sk-abcdefghijklmnopqrstuvwx"})
    assert leaked == "***redacted***"


async def test_an_audit_write_failure_does_not_fail_the_operation() -> None:
    """The privileged action already happened; losing its result helps nobody."""
    from app.services.audit import AuditAction, AuditService

    class BrokenRepository:
        async def add(self, event: Any) -> Any:
            raise RuntimeError("database unavailable")

    recorded = await AuditService(BrokenRepository()).record(  # type: ignore[arg-type]
        AuditAction.OPTIMIZATION_APPROVED,
        tenant_id=TENANT,
        resource_type="optimization_recommendation",
        resource_id="rec-1",
    )
    assert recorded is None  # reported as failed, not raised
