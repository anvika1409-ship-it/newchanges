"""Unit and integration tests for the Supply Chain AI Workload.

Test scenarios covered:
1. Normal recommendation       — healthy stock, reliable supplier, within budget
2. Insufficient inventory       — stockout deficit, replenishment computed
3. Supplier risk               — unreliable/high-risk supplier, mitigation trade-off
4. Budget pressure             — cost exceeds budget, cost mitigation trade-off
5. High-risk recommendation    — severe deficit/switch, requires_approval = True
6. Missing data handling       — validation errors on malformed/missing payloads
7. Service integration         — end-to-end execution via AIExecutionService

Tests use ``MockModelGateway`` — no live LLM or external network calls
(AI_DEVELOPMENT_RULES.md section 25).
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

from app.intelligence.supply_chain_optimizer import SupplyChainOptimizer
from app.services.execution import AIExecutionService
from app.telemetry.emitter import TelemetryEmitter
from app.telemetry.tracker import CostTracker
from app.workloads.supply_chain import SupplyChainWorkflow


# ── Sample Data Fixtures ──────────────────────────────────────────


def _sample_inventory() -> list[dict[str, Any]]:
    return [
        {
            "item_id": "PART-A100",
            "name": "Hydraulic Valve Spool",
            "current_stock": 500.0,
            "safety_stock": 200.0,
            "reorder_point": 300.0,
            "unit_cost": 45.0,
        },
        {
            "item_id": "PART-B200",
            "name": "Precision Bearing 6205",
            "current_stock": 1200.0,
            "safety_stock": 400.0,
            "reorder_point": 600.0,
            "unit_cost": 15.0,
        },
    ]


def _sample_demand() -> dict[str, float]:
    return {
        "PART-A100": 100.0,
        "PART-B200": 200.0,
    }


def _sample_suppliers() -> list[dict[str, Any]]:
    return [
        {
            "supplier_id": "SUP-001",
            "name": "Alpha Precision Components",
            "items_supplied": ["PART-A100"],
            "unit_price": 42.50,
            "lead_time_days": 5,
            "reliability_score": 0.98,
            "risk_score": 0.05,
            "capacity": 5000.0,
            "is_primary": True,
        },
        {
            "supplier_id": "SUP-002",
            "name": "Beta Fasteners & Bearings",
            "items_supplied": ["PART-B200"],
            "unit_price": 14.00,
            "lead_time_days": 4,
            "reliability_score": 0.95,
            "risk_score": 0.08,
            "capacity": 10000.0,
            "is_primary": True,
        },
    ]


def _sample_logistics() -> list[dict[str, Any]]:
    return [
        {
            "route_id": "LOG-STD-01",
            "origin": "Central Warehouse",
            "destination": "Assembly Plant 1",
            "shipping_mode": "Standard Ground",
            "cost_per_unit": 2.50,
            "transit_days": 3,
            "reliability": 0.96,
            "risk_score": 0.04,
        },
        {
            "route_id": "LOG-EXP-02",
            "origin": "Central Warehouse",
            "destination": "Assembly Plant 1",
            "shipping_mode": "Expedited Air",
            "cost_per_unit": 8.00,
            "transit_days": 1,
            "reliability": 0.99,
            "risk_score": 0.02,
        },
    ]


def _sample_lead_time() -> dict[str, Any]:
    return {
        "max_acceptable_lead_time_days": 10,
        "target_fulfillment_days": 7,
    }


# ── Test Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def mock_gateway() -> MockModelGateway:
    canned = json.dumps(
        {
            "explanation": (
                "Supply chain optimization analysis recommends proactive replenishment "
                "from primary suppliers via Standard Ground to minimize landed cost "
                "while maintaining target buffer stock."
            )
        }
    )
    return MockModelGateway(canned_content=canned, report_usage=True)


@pytest.fixture
def telemetry() -> TelemetryEmitter:
    return TelemetryEmitter()


@pytest.fixture
def cost_tracker() -> CostTracker:
    return CostTracker()


@pytest.fixture
def workflow(
    mock_gateway: MockModelGateway,
    telemetry: TelemetryEmitter,
    cost_tracker: CostTracker,
) -> SupplyChainWorkflow:
    return SupplyChainWorkflow(
        model_gateway=mock_gateway,
        telemetry=telemetry,
        cost_tracker=cost_tracker,
        default_model="supply-chain-optimizer-model",
    )


@pytest.fixture
def execution_service(
    mock_gateway: MockModelGateway,
    telemetry: TelemetryEmitter,
    cost_tracker: CostTracker,
) -> AIExecutionService:
    return AIExecutionService(
        model_gateway=mock_gateway,
        telemetry=telemetry,
        cost_tracker=cost_tracker,
        default_model="supply-chain-optimizer-model",
    )


# ── 1. Normal Recommendation ─────────────────────────────────────


class TestNormalRecommendation:
    """Healthy inventory, reliable suppliers, no budget overruns."""

    async def test_normal_recommendation_execution(
        self, workflow: SupplyChainWorkflow, mock_gateway: MockModelGateway
    ) -> None:
        payload = {
            "inventory": _sample_inventory(),
            "demand": _sample_demand(),
            "suppliers": _sample_suppliers(),
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
            "budget_limit": 50000.0,
        }

        result = await workflow.execute("exec-sc-001", payload)

        assert result["status"] == "completed"
        assert result["risk_level"] == "low"
        assert result["requires_approval"] is False
        assert result["budget_pressure"] is False
        assert result["inventory_status"]["stockout_items"] == []
        assert mock_gateway.call_count == 0  # Routine case: zero LLM calls

    async def test_normal_recommendation_formatting(
        self, workflow: SupplyChainWorkflow
    ) -> None:
        payload = {
            "inventory": _sample_inventory(),
            "demand": _sample_demand(),
            "suppliers": _sample_suppliers(),
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
        }

        raw = await workflow.execute("exec-sc-002", payload)
        formatted = await workflow.format_output(raw)

        assert formatted["workflow_type"] == "supply_chain"
        assert formatted["status"] == "completed"
        assert formatted["data_quality"] == "ACTUAL"


# ── 2. Insufficient Inventory ────────────────────────────────────


class TestInsufficientInventory:
    """Current inventory below safety threshold / critical stockout."""

    async def test_detects_inventory_deficit_and_replenishes(
        self, workflow: SupplyChainWorkflow, mock_gateway: MockModelGateway
    ) -> None:
        # Item A has 50 in stock vs 200 safety stock and 100 demand -> deficit of 250 units
        depleted_inventory = [
            {
                "item_id": "PART-A100",
                "name": "Hydraulic Valve Spool",
                "current_stock": 50.0,
                "safety_stock": 200.0,
                "reorder_point": 300.0,
                "unit_cost": 45.0,
            }
        ]
        payload = {
            "inventory": depleted_inventory,
            "demand": {"PART-A100": 100.0},
            "suppliers": _sample_suppliers(),
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
        }

        result = await workflow.execute("exec-sc-010", payload)

        assert result["status"] == "completed"
        assert "PART-A100" in result["inventory_status"]["stockout_items"]
        assert result["inventory_status"]["total_shortage_units"] > 0
        assert mock_gateway.call_count == 1  # Complex stockout triggers LLM reasoning

    async def test_allocates_correct_supplier(
        self, workflow: SupplyChainWorkflow
    ) -> None:
        depleted_inventory = [
            {
                "item_id": "PART-A100",
                "current_stock": 10.0,
                "safety_stock": 100.0,
                "reorder_point": 150.0,
                "unit_cost": 45.0,
            }
        ]
        payload = {
            "inventory": depleted_inventory,
            "demand": {"PART-A100": 50.0},
            "suppliers": _sample_suppliers(),
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
        }

        result = await workflow.execute("exec-sc-011", payload)
        allocations = result["supplier_allocation"]

        assert len(allocations) >= 1
        assert allocations[0]["selected_supplier_id"] == "SUP-001"
        assert allocations[0]["order_quantity"] > 0


# ── 3. Supplier Risk ─────────────────────────────────────────────


class TestSupplierRisk:
    """Supplier has elevated risk score / low reliability."""

    async def test_elevated_supplier_risk_handling(
        self, workflow: SupplyChainWorkflow, mock_gateway: MockModelGateway
    ) -> None:
        risky_suppliers = [
            {
                "supplier_id": "SUP-RISKY-01",
                "name": "High-Risk Offshore Supplier",
                "items_supplied": ["PART-A100"],
                "unit_price": 30.00,
                "lead_time_days": 21,
                "reliability_score": 0.65,
                "risk_score": 0.75,  # High risk > 0.4
                "capacity": 5000.0,
            }
        ]
        depleted_inventory = [
            {
                "item_id": "PART-A100",
                "current_stock": 20.0,
                "safety_stock": 100.0,
                "reorder_point": 150.0,
                "unit_cost": 45.0,
            }
        ]
        payload = {
            "inventory": depleted_inventory,
            "demand": {"PART-A100": 50.0},
            "suppliers": risky_suppliers,
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
        }

        result = await workflow.execute("exec-sc-020", payload)

        assert result["risk_level"] in ("high", "critical")
        assert result["requires_approval"] is True
        assert result["recommendation_type"] in ("supplier_switch", "replenishment")
        assert mock_gateway.call_count == 1


# ── 4. Budget Pressure ───────────────────────────────────────────


class TestBudgetPressure:
    """Landed cost exceeds budget constraints."""

    async def test_detects_budget_overrun(
        self, workflow: SupplyChainWorkflow, mock_gateway: MockModelGateway
    ) -> None:
        # Large order: 1000 units @ ~$45 + $2.50 = ~$47,500
        depleted_inventory = [
            {
                "item_id": "PART-A100",
                "current_stock": 0.0,
                "safety_stock": 800.0,
                "reorder_point": 1000.0,
                "unit_cost": 45.0,
            }
        ]
        payload = {
            "inventory": depleted_inventory,
            "demand": {"PART-A100": 200.0},
            "suppliers": _sample_suppliers(),
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
            "budget_limit": 5000.0,  # Tight budget of $5k vs ~$45k needed
        }

        result = await workflow.execute("exec-sc-030", payload)

        assert result["budget_pressure"] is True
        assert result["budget_overrun_usd"] > 0
        assert result["requires_approval"] is True
        assert mock_gateway.call_count == 1


# ── 5. High-Risk Recommendation ─────────────────────────────────


class TestHighRiskRecommendation:
    """Severe stockouts and critical operational risks require policy approval."""

    async def test_high_risk_produces_recommendation_only(
        self, workflow: SupplyChainWorkflow
    ) -> None:
        # Multiple critical stockouts with huge deficits
        critical_inventory = [
            {"item_id": f"PART-{i}", "current_stock": 0.0, "safety_stock": 500.0, "unit_cost": 50.0}
            for i in range(5)
        ]
        payload = {
            "inventory": critical_inventory,
            "demand": {f"PART-{i}": 200.0 for i in range(5)},
            "suppliers": _sample_suppliers(),
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
        }

        result = await workflow.execute("exec-sc-040", payload)

        assert result["risk_level"] in ("high", "critical")
        assert result["requires_approval"] is True
        # Must clearly include warning prefix
        assert "RECOMMENDATION ONLY" in result["recommended_action"]


# ── 6. Missing Data Handling ────────────────────────────────────


class TestMissingDataHandling:
    """Validation errors when inputs are missing or malformed."""

    async def test_missing_inventory(self, workflow: SupplyChainWorkflow) -> None:
        payload = {
            "demand": _sample_demand(),
            "suppliers": _sample_suppliers(),
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
        }
        val = await workflow.validate_input(payload)
        assert val.is_valid is False
        assert any("inventory" in e for e in val.errors)

    async def test_missing_suppliers(self, workflow: SupplyChainWorkflow) -> None:
        payload = {
            "inventory": _sample_inventory(),
            "demand": _sample_demand(),
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
        }
        val = await workflow.validate_input(payload)
        assert val.is_valid is False
        assert any("suppliers" in e for e in val.errors)

    async def test_none_input(self, workflow: SupplyChainWorkflow) -> None:
        val = await workflow.validate_input(None)  # type: ignore[arg-type]
        assert val.is_valid is False

    async def test_empty_suppliers_list(self, workflow: SupplyChainWorkflow) -> None:
        payload = {
            "inventory": _sample_inventory(),
            "demand": _sample_demand(),
            "suppliers": [],
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
        }
        val = await workflow.validate_input(payload)
        assert val.is_valid is False
        assert any("suppliers" in e for e in val.errors)


# ── 7. AI Execution Service Integration & Telemetry ──────────────


class TestServiceIntegration:
    """End-to-end integration through AIExecutionService."""

    async def test_service_execute_supply_chain(
        self, execution_service: AIExecutionService, telemetry: TelemetryEmitter
    ) -> None:
        payload = {
            "inventory": _sample_inventory(),
            "demand": _sample_demand(),
            "suppliers": _sample_suppliers(),
            "logistics": _sample_logistics(),
            "lead_time": _sample_lead_time(),
        }

        result = await execution_service.execute(
            workflow_type="supply_chain",
            input_data=payload,
        )

        assert result["status"] == "completed"
        assert result["workflow_type"] == "supply_chain"
        assert "inventory_status" in result["output"]

        # Check telemetry emission
        events = [e["event_type"] for e in telemetry.get_events(result["execution_id"])]
        assert "workflow.started" in events
        assert "workflow.step_completed" in events
        assert "workflow.completed" in events

    async def test_service_rejects_empty_payload(
        self, execution_service: AIExecutionService, telemetry: TelemetryEmitter
    ) -> None:
        result = await execution_service.execute(
            workflow_type="supply_chain",
            input_data={},
        )

        assert result["status"] == "failed"
        events = [e["event_type"] for e in telemetry.get_events(result["execution_id"])]
        assert "workflow.failed" in events
