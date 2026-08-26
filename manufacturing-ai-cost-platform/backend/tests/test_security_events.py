"""Security monitoring tests.

SECURITY.md section 20 requires authentication failures, authorization failures
and tenant-isolation attempts to be observable. An unobservable denial is a
successful probe from the attacker's point of view.

These tests also assert the negative half: no credential ever reaches a log
record (AI_DEVELOPMENT_RULES.md section 27).
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.security.events import SecurityEvent, record_security_event
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType
from tests.security_app import create_security_test_app

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
PLANT_1 = "plant-1"
PLANT_2 = "plant-2"


@pytest.fixture
def adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_security_test_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
def security_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Capture at WARNING, where every security event is emitted.

    Yields the fixture rather than ``caplog.records``: pytest swaps that list
    between the setup and call phases, so a list captured here would stay empty.
    """
    with caplog.at_level(logging.WARNING):
        yield caplog


def _events(log: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.security_event
        for record in log.records
        if hasattr(record, "security_event")
    ]


def _reasons(log: pytest.LogCaptureFixture, event: SecurityEvent) -> list[str]:
    return [
        record.reason
        for record in log.records
        if getattr(record, "security_event", None) == event
    ]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# Authentication failures
# ===========================================================================
async def test_a_missing_token_is_recorded(
    client: AsyncClient, security_log: pytest.LogCaptureFixture
) -> None:
    await client.get("/whoami")
    assert SecurityEvent.AUTHENTICATION_FAILED in _events(security_log)


async def test_an_invalid_token_is_recorded(
    client: AsyncClient, security_log: pytest.LogCaptureFixture
) -> None:
    await client.get("/whoami", headers=_auth("not-a-jwt"))

    matching = [
        record
        for record in security_log.records
        if getattr(record, "security_event", None) == SecurityEvent.AUTHENTICATION_FAILED
    ]
    assert matching
    assert matching[-1].reason == "invalid_token"


async def test_an_expired_token_is_recorded_distinctly(
    client: AsyncClient,
    adapter: DevelopmentIdentityAdapter,
    security_log: pytest.LogCaptureFixture,
) -> None:
    """Monitoring should be able to separate stale credentials from attacks."""
    token = adapter.issue_token(
        subject="user-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
        issued_at=int(time.time()) - 3600,
        expires_in_seconds=60,
    )
    await client.get("/whoami", headers=_auth(token))

    assert "token_expired" in _reasons(security_log, SecurityEvent.AUTHENTICATION_FAILED)


# ===========================================================================
# Authorization failures
# ===========================================================================
async def test_a_wrong_role_is_recorded(
    client: AsyncClient,
    adapter: DevelopmentIdentityAdapter,
    security_log: pytest.LogCaptureFixture,
) -> None:
    token = adapter.issue_token(
        subject="viewer-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A),),
    )
    await client.get("/budget-manage", headers=_auth(token))

    matching = [
        record
        for record in security_log.records
        if getattr(record, "security_event", None) == SecurityEvent.AUTHORIZATION_DENIED
    ]
    assert matching
    assert matching[-1].reason == "permission_not_granted"
    assert matching[-1].required_permission == "budget:manage"


async def test_an_out_of_scope_plant_is_recorded(
    client: AsyncClient,
    adapter: DevelopmentIdentityAdapter,
    security_log: pytest.LogCaptureFixture,
) -> None:
    token = adapter.issue_token(
        subject="pm-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, PLANT_1),),
    )
    await client.get("/cost-scope", params={"plant_id": PLANT_2}, headers=_auth(token))

    assert "plant_out_of_scope" in _reasons(security_log, SecurityEvent.AUTHORIZATION_DENIED)


# ===========================================================================
# Tenant isolation
# ===========================================================================
async def test_a_cross_tenant_attempt_is_recorded(
    client: AsyncClient,
    adapter: DevelopmentIdentityAdapter,
    security_log: pytest.LogCaptureFixture,
) -> None:
    token = adapter.issue_token(
        subject="admin-a",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )
    await client.get(
        "/budgets-by-permission/b", params={"tenant_id": TENANT_B}, headers=_auth(token)
    )
    assert SecurityEvent.TENANT_ISOLATION_VIOLATION in _events(security_log)


async def test_a_client_tenant_override_is_recorded_distinctly(
    client: AsyncClient,
    adapter: DevelopmentIdentityAdapter,
    security_log: pytest.LogCaptureFixture,
) -> None:
    token = adapter.issue_token(
        subject="admin-a",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),),
    )
    await client.get("/cost-summary", params={"tenant_id": TENANT_B}, headers=_auth(token))

    assert "client_supplied_tenant_override" in _reasons(
        security_log, SecurityEvent.TENANT_ISOLATION_VIOLATION
    )


# ===========================================================================
# No credential ever reaches a log record
# ===========================================================================
async def test_no_log_record_carries_the_bearer_token(
    client: AsyncClient,
    adapter: DevelopmentIdentityAdapter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = adapter.issue_token(
        subject="viewer-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A),),
    )
    with caplog.at_level(logging.DEBUG):
        await client.get("/budget-manage", headers=_auth(token))

    for record in caplog.records:
        rendered = record.getMessage() + repr(record.__dict__)
        assert token not in rendered


def test_forbidden_fields_are_dropped_even_if_a_caller_passes_them(
    security_log: pytest.LogCaptureFixture,
) -> None:
    """A future call site must not be able to log a credential by accident."""
    record_security_event(
        SecurityEvent.AUTHENTICATION_FAILED,
        reason="test",
        token="super-secret-token",  # noqa: S106 - the value under test
        subject="user-1",
    )

    emitted = security_log.records[-1]
    assert not hasattr(emitted, "token")
    assert emitted.subject == "user-1"


def test_none_valued_fields_are_omitted(
    security_log: pytest.LogCaptureFixture,
) -> None:
    record_security_event(
        SecurityEvent.AUTHORIZATION_DENIED,
        reason="test",
        resource_plant_id=None,
        resource_department_id="dept-1",
    )

    emitted = security_log.records[-1]
    assert not hasattr(emitted, "resource_plant_id")
    assert emitted.resource_department_id == "dept-1"
