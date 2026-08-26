"""Unit tests for the Predictive Maintenance workflow.

All five required test scenarios:
  1. Normal machine           — no anomaly, no LLM, cost = $0
  2. Anomalous machine        — single-metric anomaly, deterministic
  3. Complex incident         — multi-metric anomaly, LLM reasoning
  4. High-risk recommendation — severe anomaly, requires_approval = True
  5. Missing data             — validation failure

Tests use ``MockModelGateway`` — no live LLM or network calls
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

from app.services.execution import AIExecutionService
from app.telemetry.emitter import TelemetryEmitter
from app.telemetry.tracker import CostTracker
from app.workloads.predictive_maintenance import PredictiveMaintenanceWorkflow


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def mock_gateway() -> MockModelGateway:
    """MockModelGateway with a canned JSON root-cause response."""
    gateway = MockModelGateway(
        canned_content=json.dumps(
            {
                "explanation": (
                    "Correlated temperature and vibration anomalies suggest "
                    "bearing wear in the main drive assembly. Recommend immediate "
                    "inspection and bearing replacement."
                )
            }
        ),
        report_usage=True,
    )
    return gateway


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
) -> PredictiveMaintenanceWorkflow:
    return PredictiveMaintenanceWorkflow(
        model_gateway=mock_gateway,
        telemetry=telemetry,
        cost_tracker=cost_tracker,
        default_model="test-model",
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
        default_model="test-model",
    )


def _normal_readings() -> dict[str, float]:
    """Sensor readings all within normal range."""
    return {
        "temperature": 65.0,
        "vibration": 2.0,
        "pressure": 30.0,
        "rpm": 1500.0,
        "power_consumption": 75.0,
    }


def _single_anomaly_readings() -> dict[str, float]:
    """One sensor (vibration) anomalous, rest normal."""
    return {
        "temperature": 65.0,
        "vibration": 5.0,  # z = 3.75 > threshold 2.0
        "pressure": 30.0,
        "rpm": 1500.0,
        "power_consumption": 75.0,
    }


def _complex_anomaly_readings() -> dict[str, float]:
    """Multiple sensors anomalous (triggers LLM reasoning)."""
    return {
        "temperature": 120.0,  # z = 5.5 > threshold 2.5
        "vibration": 5.0,  # z = 3.75 > threshold 2.0
        "pressure": 55.0,  # z = 5.0 > threshold 2.5
        "rpm": 1500.0,
        "power_consumption": 75.0,
    }


def _severe_anomaly_readings() -> dict[str, float]:
    """Severe multi-metric anomaly → high/critical risk."""
    return {
        "temperature": 200.0,  # z = 13.5
        "vibration": 10.0,  # z = 10.0
        "pressure": 100.0,  # z = 14.0
        "rpm": 2500.0,  # z = 5.0
        "power_consumption": 75.0,
    }


def _event_types(telemetry: TelemetryEmitter, execution_id: str) -> list[str]:
    """Extract event types for a given execution."""
    return [e["event_type"] for e in telemetry.get_events(execution_id)]


# ── 1. Normal machine ───────────────────────────────────────────


class TestNormalMachine:
    """All sensors in normal range → no anomaly, no LLM, zero cost."""

    async def test_returns_preventive_recommendation(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-001",
            "sensor_readings": _normal_readings(),
        }
        result = await workflow.execute("exec-001", input_data)

        assert result["recommendation_type"] == "preventive"
        assert result["risk_level"] == "low"
        assert result["requires_approval"] is False

    async def test_no_llm_called(
        self,
        mock_gateway: MockModelGateway,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-001",
            "sensor_readings": _normal_readings(),
        }
        await workflow.execute("exec-002", input_data)

        assert mock_gateway.call_count == 0

    async def test_zero_cost(
        self,
        cost_tracker: CostTracker,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-001",
            "sensor_readings": _normal_readings(),
        }
        await workflow.execute("exec-003", input_data)

        assert cost_tracker.get_total_cost("exec-003") == 0.0
        assert cost_tracker.get_records("exec-003") == []

    async def test_data_quality_actual(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-001",
            "sensor_readings": _normal_readings(),
        }
        result = await workflow.execute("exec-004", input_data)

        assert result["data_quality"] == "ACTUAL"

    async def test_telemetry_emitted(
        self,
        telemetry: TelemetryEmitter,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-001",
            "sensor_readings": _normal_readings(),
        }
        await workflow.execute("exec-005", input_data)

        events = _event_types(telemetry, "exec-005")
        assert "workflow.step_completed" in events

    async def test_format_output(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-001",
            "sensor_readings": _normal_readings(),
        }
        raw = await workflow.execute("exec-006", input_data)
        formatted = await workflow.format_output(raw)

        assert formatted["machine_id"] == "MACHINE-001"
        assert formatted["recommendation_type"] == "preventive"
        assert formatted["data_quality"] == "ACTUAL"
        assert "anomaly_details" in formatted

    async def test_through_execution_service(
        self, execution_service: AIExecutionService
    ) -> None:
        """End-to-end through the service layer."""
        result = await execution_service.execute(
            workflow_type="predictive_maintenance",
            input_data={
                "machine_id": "MACHINE-001",
                "sensor_readings": _normal_readings(),
            },
        )

        assert result["status"] == "completed"
        assert result["output"]["recommendation_type"] == "preventive"
        assert result["cost_usd"] == 0.0


# ── 2. Anomalous machine ────────────────────────────────────────


class TestAnomalousMachine:
    """Single-metric anomaly → deterministic recommendation, no LLM."""

    async def test_anomaly_detected(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-002",
            "sensor_readings": _single_anomaly_readings(),
        }
        result = await workflow.execute("exec-010", input_data)

        assert result["anomaly_details"]["is_anomalous"] is True
        assert "vibration" in result["anomaly_details"]["anomalous_metrics"]

    async def test_condition_based_or_corrective_recommendation(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-002",
            "sensor_readings": _single_anomaly_readings(),
        }
        result = await workflow.execute("exec-011", input_data)

        # Single metric anomaly with high z-score produces corrective
        # recommendation (anomaly_score >= HIGH_RISK_ANOMALY_SCORE).
        assert result["recommendation_type"] in ("condition_based", "corrective")

    async def test_no_llm_for_simple_anomaly(
        self,
        mock_gateway: MockModelGateway,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        """Single-metric anomaly uses deterministic recommendation, no LLM."""
        input_data = {
            "machine_id": "MACHINE-002",
            "sensor_readings": _single_anomaly_readings(),
        }
        await workflow.execute("exec-012", input_data)

        assert mock_gateway.call_count == 0

    async def test_description_contains_metric(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-002",
            "sensor_readings": _single_anomaly_readings(),
        }
        result = await workflow.execute("exec-013", input_data)

        assert "vibration" in result["description"].lower()

    async def test_data_quality_actual_without_llm(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        """Without LLM, data quality stays ACTUAL."""
        input_data = {
            "machine_id": "MACHINE-002",
            "sensor_readings": _single_anomaly_readings(),
        }
        result = await workflow.execute("exec-014", input_data)

        assert result["data_quality"] == "ACTUAL"

    async def test_telemetry_includes_anomaly_detection(
        self,
        telemetry: TelemetryEmitter,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-002",
            "sensor_readings": _single_anomaly_readings(),
        }
        await workflow.execute("exec-015", input_data)

        events = telemetry.get_events("exec-015")
        step_events = [
            e for e in events if e["event_type"] == "workflow.step_completed"
        ]
        steps = [e["data"]["step"] for e in step_events]
        assert "anomaly_detection" in steps
        assert "risk_classification" in steps


# ── 3. Complex incident ─────────────────────────────────────────


class TestComplexIncident:
    """Multi-metric anomaly → LLM reasoning via ModelGateway."""

    async def test_llm_called(
        self,
        mock_gateway: MockModelGateway,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-003",
            "machine_type": "CNC Lathe",
            "sensor_readings": _complex_anomaly_readings(),
            "maintenance_history": [
                {"event_id": "E1", "date": "2026-07-15", "type": "preventive", "description": "Bearing replacement"},
            ],
        }
        await workflow.execute("exec-020", input_data)

        assert mock_gateway.call_count == 1

    async def test_prompt_has_system_user_separation(
        self,
        mock_gateway: MockModelGateway,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        """Prompt must separate system instructions from user data (SECURITY.md)."""
        input_data = {
            "machine_id": "MACHINE-003",
            "sensor_readings": _complex_anomaly_readings(),
        }
        await workflow.execute("exec-021", input_data)

        request = mock_gateway.get_request(0)
        roles = [m.role for m in request.messages]
        assert Role.SYSTEM in roles
        assert Role.USER in roles

    async def test_prompt_uses_json_response_format(
        self,
        mock_gateway: MockModelGateway,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        """LLM call uses structured JSON output format."""
        input_data = {
            "machine_id": "MACHINE-003",
            "sensor_readings": _complex_anomaly_readings(),
        }
        await workflow.execute("exec-022", input_data)

        request = mock_gateway.get_request(0)
        assert request.response_format == "json_object"

    async def test_cost_tracked(
        self,
        cost_tracker: CostTracker,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-003",
            "sensor_readings": _complex_anomaly_readings(),
        }
        await workflow.execute("exec-023", input_data)

        records = cost_tracker.get_records("exec-023")
        assert len(records) >= 1
        assert records[0]["cost_type"] == "llm_inference"

    async def test_data_quality_estimated(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        """LLM output is marked as ESTIMATED."""
        input_data = {
            "machine_id": "MACHINE-003",
            "sensor_readings": _complex_anomaly_readings(),
        }
        result = await workflow.execute("exec-024", input_data)

        assert result["data_quality"] == "ESTIMATED"

    async def test_telemetry_includes_llm_events(
        self,
        telemetry: TelemetryEmitter,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-003",
            "sensor_readings": _complex_anomaly_readings(),
        }
        await workflow.execute("exec-025", input_data)

        events = _event_types(telemetry, "exec-025")
        assert "llm.call_started" in events
        assert "llm.call_completed" in events
        assert "cost.recorded" in events

    async def test_model_used_recorded(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-003",
            "sensor_readings": _complex_anomaly_readings(),
        }
        result = await workflow.execute("exec-026", input_data)

        assert result["model_used"] == "test-model"

    async def test_maintenance_history_in_context(
        self,
        mock_gateway: MockModelGateway,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        """Maintenance history is included in the LLM prompt as user content."""
        history = [
            {"event_id": "E1", "date": "2026-06-01", "type": "corrective", "description": "Motor replacement"},
        ]
        input_data = {
            "machine_id": "MACHINE-003",
            "sensor_readings": _complex_anomaly_readings(),
            "maintenance_history": history,
        }
        await workflow.execute("exec-027", input_data)

        user_messages = [
            m for m in mock_gateway.get_request(0).messages if m.role == Role.USER
        ]
        assert len(user_messages) == 1
        content = user_messages[0].content
        assert "Motor replacement" in content

    async def test_context_filtered_to_last_10(
        self,
        mock_gateway: MockModelGateway,
        workflow: PredictiveMaintenanceWorkflow,
    ) -> None:
        """Maintenance history is filtered to the last 10 events."""
        history = [
            {"event_id": f"E{i}", "date": f"2026-01-{i:02d}", "type": "preventive", "description": f"Event {i}"}
            for i in range(1, 21)  # 20 events
        ]
        input_data = {
            "machine_id": "MACHINE-003",
            "sensor_readings": _complex_anomaly_readings(),
            "maintenance_history": history,
        }
        await workflow.execute("exec-028", input_data)

        user_content = mock_gateway.get_request(0).messages[-1].content
        parsed = json.loads(user_content)
        assert len(parsed["recent_maintenance_history"]) == 10
        # Should contain the last 10 (events 11-20).
        assert parsed["recent_maintenance_history"][0]["event_id"] == "E11"


# ── 4. High-risk recommendation ────────────────────────────────


class TestHighRiskRecommendation:
    """Severe anomaly → high risk → recommendation only, no auto-action."""

    async def test_requires_approval(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-004",
            "sensor_readings": _severe_anomaly_readings(),
        }
        result = await workflow.execute("exec-030", input_data)

        assert result["requires_approval"] is True

    async def test_risk_level_high_or_critical(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-004",
            "sensor_readings": _severe_anomaly_readings(),
        }
        result = await workflow.execute("exec-031", input_data)

        assert result["risk_level"] in ("high", "critical")

    async def test_action_text_includes_approval_warning(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        """High-risk actions must clearly state they are recommendation-only."""
        input_data = {
            "machine_id": "MACHINE-004",
            "sensor_readings": _severe_anomaly_readings(),
        }
        result = await workflow.execute("exec-032", input_data)

        assert "approval" in result["recommended_action"].lower()

    async def test_corrective_recommendation_type(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-004",
            "sensor_readings": _severe_anomaly_readings(),
        }
        result = await workflow.execute("exec-033", input_data)

        assert result["recommendation_type"] == "corrective"

    async def test_high_risk_through_service(
        self, execution_service: AIExecutionService
    ) -> None:
        """End-to-end: high-risk recommendation via execution service."""
        result = await execution_service.execute(
            workflow_type="predictive_maintenance",
            input_data={
                "machine_id": "MACHINE-004",
                "sensor_readings": _severe_anomaly_readings(),
            },
        )

        assert result["status"] == "completed"
        assert result["output"]["requires_approval"] is True
        assert result["output"]["risk_level"] in ("high", "critical")

    async def test_format_output_preserves_approval(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-004",
            "sensor_readings": _severe_anomaly_readings(),
        }
        raw = await workflow.execute("exec-035", input_data)
        formatted = await workflow.format_output(raw)

        assert formatted["requires_approval"] is True


# ── 5. Missing data ────────────────────────────────────────────


class TestMissingData:
    """Validation failures for missing required fields."""

    async def test_missing_machine_id(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {"sensor_readings": _normal_readings()}
        validation = await workflow.validate_input(input_data)

        assert validation.is_valid is False
        assert any("machine_id" in e for e in validation.errors)

    async def test_missing_sensor_readings(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {"machine_id": "MACHINE-005"}
        validation = await workflow.validate_input(input_data)

        assert validation.is_valid is False
        assert any("sensor_readings" in e for e in validation.errors)

    async def test_empty_input(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        validation = await workflow.validate_input({})

        assert validation.is_valid is False
        assert len(validation.errors) >= 2

    async def test_none_input(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        validation = await workflow.validate_input(None)  # type: ignore[arg-type]

        assert validation.is_valid is False

    async def test_empty_sensor_readings(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {"machine_id": "MACHINE-005", "sensor_readings": {}}
        validation = await workflow.validate_input(input_data)

        assert validation.is_valid is False
        assert any("empty" in e for e in validation.errors)

    async def test_invalid_sensor_readings_type(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {"machine_id": "MACHINE-005", "sensor_readings": "not a dict"}
        validation = await workflow.validate_input(input_data)

        assert validation.is_valid is False

    async def test_service_returns_failed_on_invalid_input(
        self, execution_service: AIExecutionService
    ) -> None:
        """Execution service returns a failed result, not an exception."""
        result = await execution_service.execute(
            workflow_type="predictive_maintenance",
            input_data={"machine_id": "MACHINE-005"},  # Missing sensor_readings.
        )

        assert result["status"] == "failed"
        assert "sensor_readings" in result.get("error", "")

    async def test_telemetry_emitted_on_validation_failure(
        self,
        telemetry: TelemetryEmitter,
        execution_service: AIExecutionService,
    ) -> None:
        result = await execution_service.execute(
            workflow_type="predictive_maintenance",
            input_data={},
        )

        assert result["status"] == "failed"
        events = _event_types(telemetry, result["execution_id"])
        assert "workflow.failed" in events

    async def test_no_llm_called_on_validation_failure(
        self,
        mock_gateway: MockModelGateway,
        execution_service: AIExecutionService,
    ) -> None:
        await execution_service.execute(
            workflow_type="predictive_maintenance",
            input_data={"machine_id": "MACHINE-005"},
        )

        assert mock_gateway.call_count == 0


# ── Workflow type validation ────────────────────────────────────


class TestWorkflowTypeValidation:
    """Execution service rejects unknown workflow types."""

    async def test_unknown_workflow_type(
        self, execution_service: AIExecutionService
    ) -> None:
        with pytest.raises(ValueError, match="Unknown workflow type"):
            await execution_service.execute(
                workflow_type="nonexistent_workflow",
                input_data={},
            )

    async def test_workflow_type_property(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        assert workflow.workflow_type == "predictive_maintenance"


# ── Confidence score ────────────────────────────────────────────


class TestConfidenceScore:
    """Confidence score is between 0 and 1 and varies with anomaly severity."""

    async def test_normal_machine_full_confidence(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-006",
            "sensor_readings": _normal_readings(),
        }
        result = await workflow.execute("exec-050", input_data)
        assert result["confidence_score"] == 1.0

    async def test_anomalous_machine_reduced_confidence(
        self, workflow: PredictiveMaintenanceWorkflow
    ) -> None:
        input_data = {
            "machine_id": "MACHINE-006",
            "sensor_readings": _single_anomaly_readings(),
        }
        result = await workflow.execute("exec-051", input_data)
        assert 0.0 < result["confidence_score"] < 1.0
