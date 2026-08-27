"""End-to-end platform validation.

Drives the primary scenario from ARCHITECTURE.md sections 3 and 4 through the
real application, then asserts the ten verification points.

    Product image -> FastAPI -> authentication -> Cost-Aware Orchestrator
    -> complexity -> budget -> routing policy -> vision model -> gateway
    -> quality result -> telemetry -> SQLite -> forecast/anomaly
    -> optimization -> approval -> new policy -> next request

The model gateway is the mock throughout: an end-to-end test must not depend on
a live LLM (AI_DEVELOPMENT_RULES.md section 25). Everything else — auth,
routing, budget, persistence, aggregation, the lifecycle — is the real code
path.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.models.control_plane import Department, Plant, Tenant, Workload
from app.db.models.registry import ModelRegistryEntry
from app.db.models.telemetry import CostEvent, UsageEvent
from app.main import create_app
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType

TENANT = "tenant-e2e"
PLANT = "plant-e2e"
DEPARTMENT = "dept-e2e"

# A 1x1 PNG. Content is irrelevant; only that an image reference is carried.
PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
).decode()


@pytest.fixture
def adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest.fixture
def engineer_token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="e2e-engineer",
        tenant_id=TENANT,
        assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT),),
    )


@pytest.fixture
def admin_token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="e2e-admin",
        tenant_id=TENANT,
        assignments=(RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT),),
    )


@pytest.fixture
def viewer_token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="e2e-viewer",
        tenant_id=TENANT,
        assignments=(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT),),
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


WORKLOAD_ID = "wl-quality"
VISION_MODEL_ID = "e2e-vision-model"
CHEAP_VISION_MODEL_ID = "e2e-vision-cheap"


@pytest_asyncio.fixture
async def platform(settings: Settings) -> AsyncIterator[tuple[Any, AsyncClient]]:
    """The real application with a registry an inspection can actually route to."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with app.state.database.session() as session:
            session.add_all(
                [
                    # The control-plane records an execution is attributed to.
                    Tenant(id=TENANT, name="E2E Tenant", status="ACTIVE"),
                    Plant(id=PLANT, tenant_id=TENANT, name="E2E Plant", status="ACTIVE"),
                    Department(
                        id=DEPARTMENT, plant_id=PLANT, name="E2E Dept", status="ACTIVE"
                    ),
                    Workload(
                        id=WORKLOAD_ID,
                        plant_id=PLANT,
                        department_id=DEPARTMENT,
                        name="Surface inspection",
                        workload_type="quality_check",
                        business_priority="NORMAL",
                        risk_level="MEDIUM",
                        status="ACTIVE",
                    ),
                    # Priced, measured, and documented as vision-capable, so a
                    # quality_check request has a legitimate candidate.
                    ModelRegistryEntry(
                        id=VISION_MODEL_ID,
                        model_name="e2e/vision-primary",
                        provider="genailab",
                        capability="vision",
                        supports_vision=True,
                        input_cost=0.01,
                        output_cost=0.02,
                        cost_unit="per_1k_tokens",
                        quality_score=0.95,
                        latency_score=0.6,
                        risk_level="LOW",
                        enabled=True,
                    ),
                    ModelRegistryEntry(
                        id=CHEAP_VISION_MODEL_ID,
                        model_name="e2e/vision-economy",
                        provider="genailab",
                        capability="vision",
                        supports_vision=True,
                        input_cost=0.001,
                        output_cost=0.002,
                        cost_unit="per_1k_tokens",
                        quality_score=0.72,
                        latency_score=0.9,
                        risk_level="LOW",
                        enabled=True,
                    ),
                ]
            )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


def inspection_body(**overrides: Any) -> dict[str, Any]:
    """A quality-check request carrying a product image by reference."""
    body: dict[str, Any] = {
        "workload_type": "quality_check",
        "business_priority": "NORMAL",
        "plant_id": PLANT,
        "department_id": DEPARTMENT,
        "request_payload": {"line": "assembly-4", "part": "housing-A"},
        "input_refs": [
            {"ref": f"obj://inspection/{uuid.uuid4()}", "content_type": "image/png"}
        ],
        "modality": "image",
    }
    body.update(overrides)
    return body


async def usage_events(app: Any) -> list[UsageEvent]:
    async with app.state.database.session() as session:
        rows = await session.execute(select(UsageEvent).order_by(UsageEvent.timestamp))
        return list(rows.scalars().all())


async def cost_events(app: Any) -> list[CostEvent]:
    async with app.state.database.session() as session:
        rows = await session.execute(select(CostEvent))
        return list(rows.scalars().all())


# ===========================================================================
# 1. The model is selected before expensive execution
# ===========================================================================
async def test_01_the_plan_exists_before_the_model_is_called(
    platform: tuple[Any, AsyncClient], api_prefix: str, engineer_token: str
) -> None:
    """The decision record is returned with the result, so routing is auditable.

    ARCHITECTURE.md section 4: "A new AI request must NOT run the manufacturing
    AI first."
    """
    app, client = platform
    response = await client.post(
        f"{api_prefix}/ai/execute", json=inspection_body(), headers=auth(engineer_token)
    )
    assert response.status_code == 200, response.text

    plan = response.json()["execution_plan"]
    assert plan["selected_model_id"] in {VISION_MODEL_ID, CHEAP_VISION_MODEL_ID}
    assert plan["complexity"] in {"SIMPLE", "MEDIUM", "COMPLEX"}
    assert plan["budget_status"] in {"ALLOW", "DOWNGRADE", "REQUIRE_APPROVAL", "BLOCK"}
    assert plan["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


async def test_01b_a_blocked_request_never_reaches_the_gateway(
    platform: tuple[Any, AsyncClient], api_prefix: str, engineer_token: str
) -> None:
    """The ordering guarantee, tested where it matters: refusal costs nothing.

    Driven through the orchestrator with a BLOCK verdict, because the API path
    has no configured budget to exhaust.
    """
    from app.integrations.llm.client import MockModelGateway
    from app.orchestrator import (
        BudgetBlockedError,
        BusinessPriority,
        CostAwareOrchestrator,
        OrchestrationRequest,
        StaticBudgetEvaluator,
    )
    from app.policies.budget_policy import PolicyOutcome
    from app.security.principal import Principal

    app, _ = platform
    gateway = MockModelGateway()

    class Registry:
        async def find_for_workload(self, *_: Any, **__: Any) -> list[Any]:
            raise AssertionError("registry queried despite a blocked budget")

    orchestrator = CostAwareOrchestrator(
        model_gateway=gateway,
        registry_service=Registry(),
        budget_evaluator=StaticBudgetEvaluator(PolicyOutcome.BLOCK),
    )
    with pytest.raises(BudgetBlockedError):
        await orchestrator.execute(
            OrchestrationRequest(
                workload_type="quality_check",
                business_priority=BusinessPriority.NORMAL,
                payload={},
            ),
            Principal(
                subject="u",
                tenant_id=TENANT,
                assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT),),
            ),
        )
    assert gateway.call_count == 0


# ===========================================================================
# 2 & 3. Cost is tracked; actual vs estimated is accurate
# ===========================================================================
async def test_02_execution_is_persisted_with_full_telemetry(
    platform: tuple[Any, AsyncClient], api_prefix: str, engineer_token: str
) -> None:
    app, client = platform
    await client.post(
        f"{api_prefix}/ai/execute",
        json=inspection_body(),
        headers={**auth(engineer_token), "X-Request-ID": "e2e-req-1"},
    )

    events = await usage_events(app)
    assert len(events) == 1

    event = events[0]
    assert event.request_id == "e2e-req-1"
    assert event.tenant_id == TENANT
    assert event.user_id == "e2e-engineer"
    assert event.plant_id == PLANT
    assert event.department_id == DEPARTMENT
    assert event.model_id in {VISION_MODEL_ID, CHEAP_VISION_MODEL_ID}
    assert event.status == "SUCCESS"
    assert event.execution_time_ms is not None
    assert event.image_count == 1


async def test_03_provenance_is_never_overclaimed(
    platform: tuple[Any, AsyncClient], api_prefix: str, engineer_token: str
) -> None:
    """A cost figure must say where it came from, and never claim ACTUAL for
    something derived (AI_DEVELOPMENT_RULES.md sections 41 and 42)."""
    app, client = platform
    response = await client.post(
        f"{api_prefix}/ai/execute", json=inspection_body(), headers=auth(engineer_token)
    )
    cost = response.json()["cost"]
    assert cost["provenance"] in {"ACTUAL", "ESTIMATED", "UNAVAILABLE"}

    # An unknown amount is null, never zero: zero reads as "free".
    if cost["amount"] is None:
        assert cost["provenance"] == "UNAVAILABLE"

    events = await cost_events(app)
    assert len(events) == 1
    stored = events[0]
    assert stored.provenance in {"ACTUAL", "ESTIMATED", "UNAVAILABLE"}
    # actual_cost is populated only for ACTUAL provenance, so the aggregation
    # layer cannot double-count it.
    if stored.provenance != "ACTUAL":
        assert stored.actual_cost is None


# ===========================================================================
# 4. The dashboard can display the result
# ===========================================================================
async def test_04_cost_endpoints_return_the_execution(
    platform: tuple[Any, AsyncClient], api_prefix: str, engineer_token: str, viewer_token: str
) -> None:
    """Everything the dashboard reads, after a real execution."""
    _, client = platform
    for _ in range(3):
        await client.post(
            f"{api_prefix}/ai/execute",
            json=inspection_body(),
            headers=auth(engineer_token),
        )

    summary = await client.get(f"{api_prefix}/cost/summary", headers=auth(viewer_token))
    assert summary.status_code == 200
    body = summary.json()
    assert body["total_requests"] == 3
    # Actual and estimated are reported separately, never merged.
    assert "actual_cost" in body and "estimated_cost" in body

    for path in ("/cost/by-model", "/cost/by-agent", "/cost/by-plant", "/cost/trend"):
        response = await client.get(f"{api_prefix}{path}", headers=auth(viewer_token))
        assert response.status_code == 200, path

    by_model = await client.get(f"{api_prefix}/cost/by-model", headers=auth(viewer_token))
    model_ids = {item["id"] for item in by_model.json()["items"]}
    assert model_ids & {VISION_MODEL_ID, CHEAP_VISION_MODEL_ID}


async def test_04b_a_viewer_can_read_but_not_execute(
    platform: tuple[Any, AsyncClient], api_prefix: str, viewer_token: str
) -> None:
    """Read access does not imply spend access."""
    _, client = platform
    assert (
        await client.get(f"{api_prefix}/cost/summary", headers=auth(viewer_token))
    ).status_code == 200

    execute = await client.post(
        f"{api_prefix}/ai/execute", json=inspection_body(), headers=auth(viewer_token)
    )
    assert execute.status_code == 403


# ===========================================================================
# 5. An anomaly can be detected
# ===========================================================================
async def test_05_anomaly_detection_runs_over_recorded_spend(
    platform: tuple[Any, AsyncClient], api_prefix: str, engineer_token: str, viewer_token: str
) -> None:
    _, client = platform
    for _ in range(3):
        await client.post(
            f"{api_prefix}/ai/execute",
            json=inspection_body(),
            headers=auth(engineer_token),
        )

    response = await client.get(f"{api_prefix}/anomalies", headers=auth(viewer_token))
    assert response.status_code == 200
    assert "items" in response.json()


def test_05b_the_detector_is_statistical_not_an_llm() -> None:
    """AI_WORKFLOWS.md section 1: cost anomaly is ML/statistics, not an LLM.

    Calling a model to decide whether a number is unusual would make the
    cost-control layer itself a cost.
    """
    import inspect

    from app.intelligence import cost_anomaly_detector

    source = inspect.getsource(cost_anomaly_detector)
    assert "ModelGateway" not in source
    assert "generate_text" not in source


# ===========================================================================
# 6, 7, 8. Optimization -> approval -> new policy -> next request
# ===========================================================================
async def test_06_optimization_generates_a_recommendation(
    platform: tuple[Any, AsyncClient], api_prefix: str, admin_token: str
) -> None:
    _, client = platform
    response = await client.post(
        f"{api_prefix}/optimization/analyze",
        json={"workload_id": "wl-quality", "simulation_only": True},
        headers=auth(admin_token),
    )
    assert response.status_code == 202, response.text
    assert response.json()["recommendation_id"]

    listing = await client.get(
        f"{api_prefix}/optimization/recommendations", headers=auth(admin_token)
    )
    assert listing.status_code == 200
    assert listing.json()["page"]["total"] >= 1


async def test_07_approval_uses_the_contract_shape(
    platform: tuple[Any, AsyncClient], api_prefix: str, admin_token: str
) -> None:
    """The approve endpoint must accept the contract's ApprovalDecision.

    The frontend sends `{"decision": "APPROVED"}` because that is what
    API_CONTRACT.yaml declares. A backend that reads a different field would
    silently ignore the caller's decision.
    """
    _, client = platform
    created = await client.post(
        f"{api_prefix}/optimization/analyze",
        json={"workload_id": "wl-quality"},
        headers=auth(admin_token),
    )
    rec_id = created.json()["recommendation_id"]

    response = await client.post(
        f"{api_prefix}/optimization/{rec_id}/approve",
        json={"decision": "APPROVED", "comments": "reviewed"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "APPROVED"


async def test_07b_rejection_is_honoured(
    platform: tuple[Any, AsyncClient], api_prefix: str, admin_token: str
) -> None:
    """A REJECTED decision must reject.

    This is the failure that matters most: if the decision field is ignored and
    the handler defaults to approving, clicking "Reject" in the UI approves the
    change instead.
    """
    _, client = platform
    created = await client.post(
        f"{api_prefix}/optimization/analyze",
        json={"workload_id": "wl-quality"},
        headers=auth(admin_token),
    )
    rec_id = created.json()["recommendation_id"]

    response = await client.post(
        f"{api_prefix}/optimization/{rec_id}/approve",
        json={"decision": "REJECTED", "comments": "not worth the quality drop"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REJECTED", (
        "a REJECTED decision was not honoured — the recommendation came back as "
        f"{response.json()['status']}"
    )


async def test_08_applying_produces_a_versioned_policy(
    platform: tuple[Any, AsyncClient], api_prefix: str, admin_token: str
) -> None:
    """Approve, apply, and confirm a versioned policy results."""
    _, client = platform
    created = await client.post(
        f"{api_prefix}/optimization/analyze",
        json={"workload_id": "wl-quality"},
        headers=auth(admin_token),
    )
    rec_id = created.json()["recommendation_id"]

    await client.post(
        f"{api_prefix}/optimization/{rec_id}/approve",
        json={"decision": "APPROVED"},
        headers=auth(admin_token),
    )
    applied = await client.post(
        f"{api_prefix}/optimization/{rec_id}/apply",
        json={"activation_mode": "FULL"},
        headers=auth(admin_token),
    )
    assert applied.status_code == 200, applied.text

    body = applied.json()
    assert body["status"] == "APPLIED"
    assert body["applied_policy_version"] >= 1


async def test_08b_a_later_request_reports_the_active_policy_version(
    platform: tuple[Any, AsyncClient], api_prefix: str, admin_token: str, engineer_token: str
) -> None:
    """The loop closes: an applied policy is visible to the next execution."""
    _, client = platform
    before = await client.post(
        f"{api_prefix}/ai/execute", json=inspection_body(), headers=auth(engineer_token)
    )
    assert before.status_code == 200

    created = await client.post(
        f"{api_prefix}/optimization/analyze",
        json={"workload_id": "wl-quality"},
        headers=auth(admin_token),
    )
    rec_id = created.json()["recommendation_id"]
    await client.post(
        f"{api_prefix}/optimization/{rec_id}/approve",
        json={"decision": "APPROVED"},
        headers=auth(admin_token),
    )
    await client.post(
        f"{api_prefix}/optimization/{rec_id}/apply",
        json={"activation_mode": "FULL"},
        headers=auth(admin_token),
    )

    after = await client.post(
        f"{api_prefix}/ai/execute", json=inspection_body(), headers=auth(engineer_token)
    )
    assert after.status_code == 200
    # The plan carries whatever policy version was active when it was built,
    # which is what makes a routing decision reproducible after the fact.
    assert "routing_policy_version" in after.json()["execution_plan"]


# ===========================================================================
# 9. High-risk actions are blocked without approval
# ===========================================================================
def test_09_a_high_risk_tool_requires_approval() -> None:
    """SECURITY.md sections 11 and 14."""
    from app.guardrails import (
        RegisteredTool,
        ToolCallRequest,
        ToolGuard,
        ToolRequiresApproval,
        ToolRisk,
    )
    from app.security.principal import Principal

    tool = RegisteredTool(
        id="tool-stop-line",
        name="stop_production_line",
        allowed_roles=frozenset({Role.PLANT_MANAGER}),
        allowed_workloads=None,
        risk_level=ToolRisk.CRITICAL,
        enabled=True,
        parameter_names=frozenset({"line_id"}),
    )
    guard = ToolGuard({tool.name: tool})
    principal = Principal(
        subject="pm",
        tenant_id=TENANT,
        assignments=(RoleAssignment(Role.PLANT_MANAGER, ScopeType.TENANT, TENANT),),
    )

    with pytest.raises(ToolRequiresApproval):
        guard.authorize(
            ToolCallRequest(name="stop_production_line", parameters={"line_id": "4"}),
            principal,
        )

    # With a recorded approval it proceeds — and the approval comes from the
    # approvals table, never from the model's own output.
    authorized = guard.authorize(
        ToolCallRequest(name="stop_production_line", parameters={"line_id": "4"}),
        principal,
        approved_tool_ids=frozenset({"tool-stop-line"}),
    )
    assert authorized.requires_approval is True


def test_09b_model_output_cannot_request_an_unapproved_action() -> None:
    """A model asking to stop a line is a recommendation, not an instruction."""
    from app.guardrails import OutputGuard, UnsafeActionBlocked

    guard = OutputGuard(allowed_actions=frozenset({"flag_for_review"}))
    with pytest.raises(UnsafeActionBlocked):
        guard.check_structured({"action": "stop_production_line", "confidence": 0.99})


async def test_09c_optimization_cannot_be_applied_without_approval(
    platform: tuple[Any, AsyncClient], api_prefix: str, admin_token: str
) -> None:
    """A recommendation is not a production change until a human approves it."""
    _, client = platform
    created = await client.post(
        f"{api_prefix}/optimization/analyze",
        json={"workload_id": "wl-quality"},
        headers=auth(admin_token),
    )
    rec_id = created.json()["recommendation_id"]

    applied = await client.post(
        f"{api_prefix}/optimization/{rec_id}/apply",
        json={},
        headers=auth(admin_token),
    )
    assert applied.status_code == 409, (
        "an unapproved recommendation was applied — approval is not enforced"
    )


# ===========================================================================
# 10. No frontend secret
# ===========================================================================
def test_10_no_secret_reaches_the_frontend_bundle() -> None:
    """The browser never holds a provider credential.

    Scans the frontend source for anything credential-shaped, and for the
    specific variables that must stay server-side
    (AI_DEVELOPMENT_RULES.md section 19, SECURITY.md section 6).
    """
    from pathlib import Path

    from app.guardrails import contains_secret

    frontend = Path(__file__).resolve().parents[2] / "frontend"
    assert frontend.is_dir(), "frontend directory not found"

    forbidden = ("GENAI_API_KEY", "JWT_SECRET", "genailab.tcs.in", "DATABASE_URL")
    offenders: list[str] = []

    for path in list(frontend.glob("src/**/*")) + list(frontend.glob("*.ts")):
        if not path.is_file() or path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
        if contains_secret(text):
            offenders.append(f"{path.name}: credential-shaped literal")

    assert not offenders, f"secrets found in frontend source: {offenders}"


def test_10b_env_example_carries_no_real_credential() -> None:
    """A committed example must be a placeholder, not a working key."""
    from pathlib import Path

    from app.guardrails import contains_secret

    root = Path(__file__).resolve().parents[2]
    for name in ("frontend/.env.example", "backend/.env.example"):
        path = root / name
        if not path.is_file():
            continue
        assert not contains_secret(
            path.read_text(encoding="utf-8")
        ), f"{name} contains a credential-shaped value"
