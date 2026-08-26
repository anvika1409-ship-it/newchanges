"""Unit and integration tests for the LangGraph Cost Investigation workflow.

Tests cover:
- Normal spend investigation (no anomaly -> clean termination)
- Cost spike investigation (anomaly detected -> drivers identified -> ModelGateway LLM reasoning -> recommendation -> savings -> policy proposal)
- Governance rule: "Do not directly activate a policy" (status="PENDING_APPROVAL", requires_approval=True)
- Deterministic termination and bounded node iterations
- Timeout guard handling
- Structured output schema conformance
"""

from __future__ import annotations

import json
from typing import Any

import pytest

try:
    from app.integrations.llm.client import MockModelGateway as BaseMockGateway
    from app.integrations.llm.interface import Role, TextGenerationRequest as ModelRequest

    class MockModelGateway(BaseMockGateway):
        def __init__(self, canned_content: str = "", report_usage: bool = True):
            super().__init__(canned_text=canned_content, report_usage=report_usage)

        @property
        def call_count(self) -> int:
            return len(self.calls)

        def get_request(self, index: int = 0):
            if not self.calls:
                raise IndexError("No calls made")
            item = self.calls[index]
            if isinstance(item, tuple):
                return item[1]
            return item

    _USE_LLM_CLIENT = True
except ImportError:
    from app.integrations.model_gateway.base import ModelRequest, Role
    from app.integrations.model_gateway.mock import MockModelGateway as BaseMockGateway

    class MockModelGateway(BaseMockGateway):  # type: ignore[no-redef]
        def get_request(self, index: int = 0):
            return self.calls[index]

    _USE_LLM_CLIENT = False

from app.intelligence.cost_investigation_graph import (
    CostInvestigationResult,
    CostInvestigationWorkflow,
)
from app.services.cost_service import CostService
from app.services.policy_service import PolicyService


@pytest.fixture
def mock_gateway() -> MockModelGateway:
    canned = json.dumps(
        {
            "root_cause": (
                "Workload routing policy statically routed all predictive maintenance tasks "
                "to advanced reasoning models (claude-3-5-sonnet), generating a 300% cost spike."
            )
        }
    )
    return MockModelGateway(canned_content=canned, report_usage=True)


@pytest.fixture
def cost_service() -> CostService:
    return CostService(default_daily_mean=50.0, default_daily_std=10.0)


@pytest.fixture
def policy_service() -> PolicyService:
    return PolicyService()


@pytest.fixture
def workflow(
    mock_gateway: MockModelGateway,
    cost_service: CostService,
    policy_service: PolicyService,
) -> CostInvestigationWorkflow:
    return CostInvestigationWorkflow(
        model_gateway=mock_gateway,
        cost_service=cost_service,
        policy_service=policy_service,
        timeout_seconds=5.0,
    )


# ── Test Suite ────────────────────────────────────────────────────


class TestCostInvestigationGraph:
    """Test suite for LangGraph Cost Investigation workflow."""

    async def test_normal_investigation_no_anomaly(
        self, workflow: CostInvestigationWorkflow, mock_gateway: MockModelGateway
    ) -> None:
        """Normal metrics produce clean diagnosis and zero LLM calls."""
        normal_metrics = {
            "cost_usd": 52.0,  # Baseline mean is 50.0
            "token_count": 10200,
            "latency_ms": 460.0,
            "request_count": 100,
        }

        result = await workflow.execute(
            scope_type="TENANT",
            scope_id="tenant-alpha",
            current_metrics=normal_metrics,
        )

        assert isinstance(result, CostInvestigationResult)
        assert result.status == "completed"
        assert result.anomaly is None
        assert result.estimated_saving == 0.0
        assert result.proposed_policy_change is None
        assert result.risk == "LOW"
        assert mock_gateway.call_count == 0  # No LLM reasoning needed for normal state

    async def test_cost_spike_investigation_full_pipeline(
        self, workflow: CostInvestigationWorkflow, mock_gateway: MockModelGateway
    ) -> None:
        """Cost spike triggers complete 8-node pipeline with LLM root-cause and policy proposal."""
        spike_metrics = {
            "cost_usd": 280.0,  # Surge vs mean 50.0
            "token_count": 45000,
            "latency_ms": 1200.0,
            "request_count": 300,
            "model_distribution": {
                "claude-3-5-sonnet": 250,
                "gpt-4o-mini": 50,
            },
        }

        result = await workflow.execute(
            request_id="test-inv-001",
            scope_type="WORKLOAD",
            scope_id="predictive_maintenance",
            current_metrics=spike_metrics,
        )

        # 1. State and schema verification
        assert isinstance(result, CostInvestigationResult)
        assert result.request_id == "test-inv-001"
        assert result.status == "completed"

        # 2. Anomaly detected
        assert result.anomaly is not None
        assert result.anomaly["anomaly_type"] == "cost_spike"
        assert result.anomaly["actual_value"] == 280.0

        # 3. Drivers identified
        assert len(result.drivers) >= 1
        driver_types = [d["driver_type"] for d in result.drivers]
        assert "expensive_model_surge" in driver_types

        # 4. LLM root-cause invoked via ModelGateway
        assert mock_gateway.call_count == 1
        assert "claude-3-5-sonnet" in result.root_cause

        # 5. Recommendation and savings
        assert len(result.recommendation) > 10
        assert result.estimated_saving > 0.0

        # 6. Risk and Policy Governance
        assert result.risk in ("MEDIUM", "HIGH")
        assert result.proposed_policy_change is not None

        # STRICT GOVERNANCE: Policy must be PENDING_APPROVAL and NOT activated
        proposal = result.proposed_policy_change
        assert proposal["status"] == "PENDING_APPROVAL"
        assert proposal["requires_approval"] is True
        assert proposal["current_model"] == "claude-3-5-sonnet"
        assert proposal["recommended_model"] == "gpt-4o-mini"
        assert proposal["estimated_monthly_saving_usd"] > 0

    async def test_policy_never_activated_autonomously(
        self, workflow: CostInvestigationWorkflow
    ) -> None:
        """Governance check: verify policy change is strictly proposals-only."""
        spike_metrics = {"cost_usd": 350.0}

        result = await workflow.execute(current_metrics=spike_metrics)

        proposal = result.proposed_policy_change
        assert proposal is not None
        assert proposal["status"] == "PENDING_APPROVAL"
        assert proposal["status"] != "ACTIVE"
        assert proposal["requires_approval"] is True

    async def test_deterministic_termination(
        self, workflow: CostInvestigationWorkflow
    ) -> None:
        """Workflow terminates cleanly at END without unbounded looping."""
        result = await workflow.execute(
            scope_type="PLANT",
            scope_id="plant-01",
            current_metrics={"cost_usd": 150.0},
        )

        assert result.status == "completed"
        assert result.scope_type == "PLANT"
        assert result.scope_id == "plant-01"

    async def test_timeout_handling(
        self,
        mock_gateway: MockModelGateway,
        cost_service: CostService,
        policy_service: PolicyService,
    ) -> None:
        """Workflow respects timeout limit when an operation is delayed."""
        # Set an ultra-short timeout of 0.0001s to force timeout branch
        fast_timeout_workflow = CostInvestigationWorkflow(
            model_gateway=mock_gateway,
            cost_service=cost_service,
            policy_service=policy_service,
            timeout_seconds=0.0001,
        )

        result = await fast_timeout_workflow.execute(
            current_metrics={"cost_usd": 200.0}
        )

        assert result.status == "timeout"
        assert result.risk == "HIGH"
        assert "timed out" in result.root_cause
