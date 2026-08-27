"""Telemetry persistence tests.

Every AI execution must produce a durable record (AI_DEVELOPMENT_RULES.md
section 8), carrying the fields ARCHITECTURE.md section 15 requires. These tests
drive the real endpoint and then read the rows back out of the database, so they
verify what was actually written rather than what was passed to a double.

The distinction under test throughout: **a limit is not a measurement, and an
unknown is not a zero.**
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
from app.db.models.registry import ModelRegistryEntry
from app.db.models.telemetry import CostEvent, UsageEvent
from app.main import create_app
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType

TENANT = "tenant-a"
SUBJECT = "engineer-1"


@pytest.fixture
def adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest.fixture
def token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject=SUBJECT,
        tenant_id=TENANT,
        assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT),),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def app_and_client(settings: Settings) -> AsyncIterator[tuple[Any, AsyncClient]]:
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
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


def _body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workload_type": "predictive_maintenance",
        "business_priority": "NORMAL",
        "request_payload": {"sensor": "vibration nominal"},
    }
    payload.update(overrides)
    return payload


async def _usage_events(app: Any) -> list[UsageEvent]:
    async with app.state.database.session() as session:
        rows = await session.execute(select(UsageEvent).order_by(UsageEvent.timestamp))
        return list(rows.scalars().all())


async def _cost_events(app: Any) -> list[CostEvent]:
    async with app.state.database.session() as session:
        rows = await session.execute(select(CostEvent))
        return list(rows.scalars().all())


# ===========================================================================
# A successful execution is recorded
# ===========================================================================
async def test_a_successful_execution_writes_one_usage_event(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    app, client = app_and_client
    response = await client.post(
        f"{api_prefix}/ai/execute", json=_body(), headers=_auth(token)
    )
    assert response.status_code == 200, response.text

    events = await _usage_events(app)
    assert len(events) == 1
    assert events[0].status == "SUCCESS"


async def test_the_record_carries_every_required_correlation_field(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    """ARCHITECTURE.md section 15's mandatory identifiers."""
    app, client = app_and_client
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(),
        headers={**_auth(token), "X-Request-ID": "telemetry-req-1"},
    )
    body = response.json()

    event = (await _usage_events(app))[0]
    assert event.request_id == "telemetry-req-1"
    assert event.trace_id is not None
    assert event.tenant_id == TENANT
    assert event.user_id == SUBJECT
    assert event.model_id == body["execution_plan"]["selected_model_id"]
    assert event.timestamp is not None


async def test_the_record_carries_the_routing_decisions(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    app, client = app_and_client
    await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(business_priority="HIGH"),
        headers=_auth(token),
    )

    event = (await _usage_events(app))[0]
    assert event.business_priority == "HIGH"
    assert event.risk_level == "MEDIUM"
    assert event.budget_decision == "ALLOW"


async def test_latency_is_measured_not_left_null(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    """Total duration and the gateway call are timed separately."""
    app, client = app_and_client
    await client.post(f"{api_prefix}/ai/execute", json=_body(), headers=_auth(token))

    event = (await _usage_events(app))[0]
    assert event.execution_time_ms is not None
    assert event.execution_time_ms >= 0
    assert event.model_latency_ms is not None


async def test_scope_is_recorded_when_supplied(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    app, client = app_and_client
    await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(plant_id="plant-1", department_id="dept-1"),
        headers=_auth(token),
    )

    event = (await _usage_events(app))[0]
    assert event.plant_id == "plant-1"
    assert event.department_id == "dept-1"


# ===========================================================================
# A limit is not a measurement
# ===========================================================================
async def test_context_tokens_is_not_the_configured_limit(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    """The gateway does not report context consumed, so it stays NULL.

    Recording ``max_context_tokens`` here would report a ceiling as consumption
    and overstate usage in every aggregate built on it.
    """
    app, client = app_and_client
    await client.post(f"{api_prefix}/ai/execute", json=_body(), headers=_auth(token))

    event = (await _usage_events(app))[0]
    assert event.context_tokens is None


async def test_tool_calls_records_calls_made_not_the_ceiling(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    """No tools are offered on this path, so zero were called."""
    app, client = app_and_client
    await client.post(f"{api_prefix}/ai/execute", json=_body(), headers=_auth(token))

    event = (await _usage_events(app))[0]
    assert event.tool_calls == 0


async def test_image_count_reflects_the_request(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    app, client = app_and_client
    await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(
            input_refs=[
                {"ref": "obj://a", "content_type": "image/png"},
                {"ref": "obj://b", "content_type": "image/jpeg"},
                {"ref": "obj://c", "content_type": "application/pdf"},
            ]
        ),
        headers=_auth(token),
    )

    event = (await _usage_events(app))[0]
    assert event.image_count == 2  # the PDF is not an image


# ===========================================================================
# Unknown is not zero
# ===========================================================================
async def test_unreported_tokens_stay_null(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    """The mock gateway reports usage; when a provider does not, NULL persists.

    Verified directly against the recorder so the provider's behaviour is the
    variable rather than the transport.
    """
    from app.orchestrator.classification import BusinessPriority, Complexity, RiskLevel
    from app.orchestrator.orchestrator import ExecutionResult
    from app.orchestrator.plan import ExecutionPlan
    from app.policies.budget_policy import PolicyOutcome
    from app.telemetry.recorder import TelemetryRecorder

    app, _ = app_and_client
    plan = ExecutionPlan(
        request_id="req-null",
        workload_type="predictive_maintenance",
        complexity=Complexity.SIMPLE,
        business_priority=BusinessPriority.NORMAL,
        risk_level=RiskLevel.LOW,
        budget_status=PolicyOutcome.ALLOW,
        tenant_id=TENANT,
        selected_model_id="m-1",
        selected_model_name="m-1",
    )
    result = ExecutionResult(
        request_id="req-null",
        trace_id=None,
        plan=plan,
        result={},
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )

    await TelemetryRecorder(app.state.database.session).record_execution(
        plan=plan, outcome="success", result=result, duration_ms=12.0
    )

    events = [e for e in await _usage_events(app) if e.request_id == "req-null"]
    assert len(events) == 1
    assert events[0].input_tokens is None
    assert events[0].output_tokens is None
    assert events[0].total_tokens is None


# ===========================================================================
# Refusals are recorded too
# ===========================================================================
async def test_a_refused_execution_is_still_recorded(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    """A registry that only records successes cannot explain spend or refusals."""
    app, client = app_and_client
    response = await client.post(
        f"{api_prefix}/ai/execute",
        json=_body(workload_type="quality_check"),
        headers=_auth(token),
    )
    assert response.status_code == 409

    events = await _usage_events(app)
    assert len(events) == 1
    assert events[0].status == "FAILURE"
    assert events[0].model_id is None  # nothing was selected


# ===========================================================================
# Cost events
# ===========================================================================
async def test_a_cost_event_is_linked_to_every_usage_event(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    app, client = app_and_client
    await client.post(f"{api_prefix}/ai/execute", json=_body(), headers=_auth(token))

    usage = await _usage_events(app)
    costs = await _cost_events(app)
    assert len(costs) == len(usage) == 1
    assert costs[0].usage_event_id == usage[0].id


async def test_unknown_cost_is_unavailable_not_zero(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    """The seeded model has no pricing, so cost is unknown — not free."""
    app, client = app_and_client
    await client.post(f"{api_prefix}/ai/execute", json=_body(), headers=_auth(token))

    cost = (await _cost_events(app))[0]
    assert cost.provenance == "UNAVAILABLE"
    assert cost.actual_cost is None


async def test_actual_cost_is_only_set_for_actual_provenance(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    """Columns are summed independently by provenance, so a value in the wrong
    one would be double-counted."""
    from app.orchestrator.classification import BusinessPriority, Complexity, RiskLevel
    from app.orchestrator.orchestrator import ExecutionResult
    from app.orchestrator.plan import ExecutionPlan
    from app.policies.budget_policy import PolicyOutcome
    from app.telemetry.recorder import TelemetryRecorder

    app, _ = app_and_client

    def _plan(request_id: str) -> ExecutionPlan:
        return ExecutionPlan(
            request_id=request_id,
            workload_type="predictive_maintenance",
            complexity=Complexity.SIMPLE,
            business_priority=BusinessPriority.NORMAL,
            risk_level=RiskLevel.LOW,
            budget_status=PolicyOutcome.ALLOW,
            tenant_id=TENANT,
        )

    recorder = TelemetryRecorder(app.state.database.session)
    for request_id, provenance in (("req-a", "ACTUAL"), ("req-e", "ESTIMATED")):
        plan = _plan(request_id)
        await recorder.record_execution(
            plan=plan,
            outcome="success",
            result=ExecutionResult(
                request_id=request_id,
                trace_id=None,
                plan=plan,
                result={},
                cost_amount=2.5,
                cost_currency="USD",
                cost_provenance=provenance,
            ),
        )

    by_provenance = {c.provenance: c for c in await _cost_events(app)}
    assert by_provenance["ACTUAL"].actual_cost == pytest.approx(2.5)
    assert by_provenance["ESTIMATED"].actual_cost is None
    assert by_provenance["ESTIMATED"].estimated_cost == pytest.approx(2.5)


async def test_a_telemetry_failure_does_not_fail_the_request(
    app_and_client: tuple[Any, AsyncClient], api_prefix: str, token: str
) -> None:
    """Losing the record must not also lose the caller's result."""
    from contextlib import asynccontextmanager

    from app.orchestrator.classification import BusinessPriority, Complexity, RiskLevel
    from app.orchestrator.plan import ExecutionPlan
    from app.policies.budget_policy import PolicyOutcome
    from app.telemetry.recorder import TelemetryRecorder

    app, _ = app_and_client

    @asynccontextmanager
    async def broken_session():
        raise RuntimeError("database unavailable")
        yield  # pragma: no cover - unreachable, satisfies the generator protocol

    plan = ExecutionPlan(
        request_id="req-broken",
        workload_type="predictive_maintenance",
        complexity=Complexity.SIMPLE,
        business_priority=BusinessPriority.NORMAL,
        risk_level=RiskLevel.LOW,
        budget_status=PolicyOutcome.ALLOW,
        tenant_id=TENANT,
    )

    recorded = await TelemetryRecorder(broken_session).record_execution(
        plan=plan, outcome="success", result=None
    )

    # Reported as failed, not raised: the caller's result survives.
    assert recorded is None
    assert not [e for e in await _usage_events(app) if e.request_id == "req-broken"]
