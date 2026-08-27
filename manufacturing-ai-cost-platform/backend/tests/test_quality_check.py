"""Tests for Manufacturing Quality Control vertical slice.

Verifies:
- Quality prompt building and structured JSON response parsing
- Multipart image upload endpoint validation and storage
- Multimodal routing and vision model dispatch through CostAwareOrchestrator
- Telemetry persistence (UsageEvent + CostEvent)
- End-to-end POST /api/v1/ai/execute flow for quality_check workloads
"""

from __future__ import annotations

import io
import json
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
from app.integrations.llm.client import MockModelGateway
from app.integrations.llm.interface import ImagePart, MultimodalGenerationRequest
from app.main import create_app
from app.orchestrator import (
    BusinessPriority,
    CostAwareOrchestrator,
    NoCompatibleModelError,
    NullBudgetEvaluator,
    OrchestrationRequest,
)
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Principal, Role, RoleAssignment, ScopeType
from app.services.model_registry import ModelRegistryService
from app.telemetry.recorder import TelemetryRecorder
from app.workloads.quality_check import (
    QualityVerdict,
    build_quality_prompt,
    parse_quality_response,
)

TENANT = "tenant-quality-test"

# Minimal valid 1x1 PNG bytes
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00"
    b"\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _vision_model(**overrides: Any) -> ModelRegistryEntry:
    defaults: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "model_name": "azure_ai/genailab-maas-Phi-3.5-vision-instruct",
        "provider": "genailab",
        "capability": "vision",
        "supports_vision": True,
        "enabled": True,
        "input_cost": 0.001,
        "output_cost": 0.002,
        "cost_unit": "USD",
    }
    defaults.update(overrides)
    return ModelRegistryEntry(**defaults)


def _llama_vision_model(**overrides: Any) -> ModelRegistryEntry:
    defaults: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "model_name": "azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct",
        "provider": "genailab",
        "capability": "vision",
        "supports_vision": True,
        "enabled": True,
        "input_cost": 0.005,
        "output_cost": 0.010,
        "cost_unit": "USD",
    }
    defaults.update(overrides)
    return ModelRegistryEntry(**defaults)


@pytest.fixture
def auth_token(settings: Settings) -> str:
    adapter = DevelopmentIdentityAdapter(settings)
    return adapter.issue_token(
        subject="inspector-1",
        tenant_id=TENANT,
        assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT),),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# 1. Quality Prompt and Parser Tests
# ===========================================================================
def test_build_quality_prompt() -> None:
    prompt = build_quality_prompt({"part_spec": "Gearbox bearing assembly with +/-0.01mm tolerance"})
    assert "quality control inspector" in prompt
    assert "Gearbox bearing assembly" in prompt
    assert "PASS" in prompt and "FAIL" in prompt


def test_parse_quality_response_valid_json() -> None:
    raw = json.dumps({"verdict": "PASS", "defect_type": None, "confidence": 0.98})
    result = parse_quality_response(raw)
    assert result.verdict == QualityVerdict.PASS
    assert result.defect_type is None
    assert result.confidence == 0.98


def test_parse_quality_response_with_defect() -> None:
    raw = json.dumps({"verdict": "FAIL", "defect_type": "surface_scratch_deep", "confidence": 0.94})
    result = parse_quality_response(raw)
    assert result.verdict == QualityVerdict.FAIL
    assert result.defect_type == "surface_scratch_deep"
    assert result.confidence == 0.94


def test_parse_quality_response_in_markdown_fences() -> None:
    raw = "```json\n" + json.dumps({"verdict": "FAIL", "defect_type": "crack", "confidence": 0.89}) + "\n```"
    result = parse_quality_response(raw)
    assert result.verdict == QualityVerdict.FAIL
    assert result.defect_type == "crack"
    assert result.confidence == 0.89


def test_parse_quality_response_fallback_heuristics() -> None:
    raw = "Based on the image inspection, the product shows no defects and passes all quality criteria."
    result = parse_quality_response(raw)
    assert result.verdict == QualityVerdict.PASS

    fail_raw = "Defect detected: major surface scratch on component body."
    fail_result = parse_quality_response(fail_raw)
    assert fail_result.verdict == QualityVerdict.FAIL


# ===========================================================================
# 2. Image Upload Endpoint Tests
# ===========================================================================
@pytest_asyncio.fixture
async def quality_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with app.state.database.session() as session:
            session.add(_vision_model())
            session.add(_llama_vision_model())

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


async def test_upload_valid_image(quality_client: AsyncClient, api_prefix: str, auth_token: str) -> None:
    files = {"file": ("part_sample.png", io.BytesIO(TINY_PNG), "image/png")}
    response = await quality_client.post(
        f"{api_prefix}/quality/upload",
        files=files,
        headers=_auth(auth_token),
    )
    assert response.status_code == 201
    body = response.json()
    assert "ref" in body
    assert body["content_type"] == "image/png"
    assert body["size_bytes"] == len(TINY_PNG)


async def test_upload_invalid_content_type(quality_client: AsyncClient, api_prefix: str, auth_token: str) -> None:
    files = {"file": ("report.pdf", io.BytesIO(b"%PDF-1.4 sample"), "application/pdf")}
    response = await quality_client.post(
        f"{api_prefix}/quality/upload",
        files=files,
        headers=_auth(auth_token),
    )
    assert response.status_code == 400


# ===========================================================================
# 3. Multimodal Orchestration Tests
# ===========================================================================
async def test_orchestrator_dispatches_multimodal_for_quality_check() -> None:
    canned_json = json.dumps({"verdict": "PASS", "defect_type": None, "confidence": 0.95})
    gateway = MockModelGateway(canned_text=canned_json)

    # In-memory fake registry with vision model
    class FakeRegistry:
        async def find_for_workload(self, workload_type: str, **_: Any) -> list[ModelRegistryEntry]:
            return [_vision_model()]

    orchestrator = CostAwareOrchestrator(
        model_gateway=gateway,
        registry_service=FakeRegistry(),
        budget_evaluator=NullBudgetEvaluator(),
    )

    principal = Principal(
        subject="inspector-1",
        tenant_id=TENANT,
        assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT),),
    )

    request = OrchestrationRequest(
        workload_type="quality_check",
        business_priority=BusinessPriority.NORMAL,
        payload={"description": "metal plate surface check"},
        image_count=1,
        image_bytes=[(TINY_PNG, "image/png")],
    )

    result = await orchestrator.execute(request, principal)

    # Verify generate_multimodal was called with ImagePart
    assert len(gateway.calls) == 1
    op_name, model_req = gateway.calls[0]
    assert op_name == "generate_multimodal"
    assert isinstance(model_req, MultimodalGenerationRequest)
    assert any(m.has_image for m in model_req.messages)

    # Verify quality result structure
    assert result.result["verdict"] == "PASS"
    assert result.result["confidence"] == 0.95
    assert result.quality_score == 0.95


async def test_quality_check_no_vision_model_raises_409() -> None:
    class EmptyRegistry:
        async def find_for_workload(self, workload_type: str, **_: Any) -> list[ModelRegistryEntry]:
            return []

    orchestrator = CostAwareOrchestrator(
        model_gateway=MockModelGateway(),
        registry_service=EmptyRegistry(),
        budget_evaluator=NullBudgetEvaluator(),
    )

    principal = Principal(
        subject="inspector-1",
        tenant_id=TENANT,
        assignments=(RoleAssignment(Role.AI_ENGINEER, ScopeType.TENANT, TENANT),),
    )

    request = OrchestrationRequest(
        workload_type="quality_check",
        business_priority=BusinessPriority.NORMAL,
        payload={"description": "check"},
    )

    with pytest.raises(NoCompatibleModelError):
        await orchestrator.execute(request, principal)


# ===========================================================================
# 4. End-to-End API Integration & Telemetry Tests
# ===========================================================================
async def test_quality_check_e2e_flow(quality_client: AsyncClient, api_prefix: str, auth_token: str) -> None:
    # 1. Upload image
    files = {"file": ("inspection_sample.png", io.BytesIO(TINY_PNG), "image/png")}
    upload_res = await quality_client.post(
        f"{api_prefix}/quality/upload",
        files=files,
        headers=_auth(auth_token),
    )
    assert upload_res.status_code == 201
    upload_data = upload_res.json()

    # 2. Execute AI workload with input_refs
    exec_payload = {
        "workload_type": "quality_check",
        "business_priority": "NORMAL",
        "modality": "image",
        "input_refs": [
            {
                "ref": upload_data["ref"],
                "content_type": upload_data["content_type"],
                "size_bytes": upload_data["size_bytes"],
            }
        ],
        "request_payload": {"component": "turbine blade"},
    }

    response = await quality_client.post(
        f"{api_prefix}/ai/execute",
        json=exec_payload,
        headers=_auth(auth_token),
    )

    assert response.status_code == 200, response.text
    data = response.json()

    # Verify execution plan
    plan = data["execution_plan"]
    assert plan["workload_type"] == "quality_check"
    assert plan["selected_model_id"] is not None
    assert plan["complexity"] in ("SIMPLE", "MEDIUM", "COMPLEX")

    # Verify result fields
    assert "verdict" in data["result"]
    assert "content" in data["result"]
    assert "usage" in data
    assert "cost" in data
    assert data["cost"]["provenance"] in ("ACTUAL", "ESTIMATED", "UNAVAILABLE")


async def test_quality_telemetry_persistence(quality_client: AsyncClient, api_prefix: str, auth_token: str) -> None:
    exec_payload = {
        "workload_type": "quality_check",
        "business_priority": "HIGH",
        "request_payload": {"batch_id": "batch-101"},
    }

    response = await quality_client.post(
        f"{api_prefix}/ai/execute",
        json=exec_payload,
        headers=_auth(auth_token),
    )
    assert response.status_code == 200
    req_id = response.json()["request_id"]

    # Verify rows in SQLite usage_events and cost_events
    app = quality_client._transport.app  # type: ignore[attr-defined]
    async with app.state.database.session() as session:
        usage_row = (
            await session.execute(select(UsageEvent).where(UsageEvent.request_id == req_id))
        ).scalar_one_or_none()

        assert usage_row is not None
        assert usage_row.status == "SUCCESS"
        assert usage_row.business_priority == "HIGH"

        cost_row = (
            await session.execute(select(CostEvent).where(CostEvent.usage_event_id == usage_row.id))
        ).scalar_one_or_none()

        assert cost_row is not None
        assert cost_row.provenance in ("ACTUAL", "ESTIMATED", "UNAVAILABLE")
