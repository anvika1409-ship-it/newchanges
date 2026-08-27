"""Predictive Maintenance workflow.

Implements the three-step PdM pipeline from AI_WORKFLOWS.md section 2:

    Step A  Anomaly Detection   (ML/statistics — no LLM)
    Step B  Root-Cause Reasoning (LLM via ModelGateway — only when complex)
    Step C  Risk Classification  (deterministic — high-risk = recommendation only)

This is a pluggable demonstration workload, not a manufacturing execution
system (AI_DEVELOPMENT_RULES.md section 9).

Design decisions:
* Simple single-metric anomalies get a deterministic recommendation (no LLM,
  zero token cost).  LLM reasoning is reserved for complex multi-metric
  anomalies where a root-cause explanation adds value.
* High-risk actions (risk_level critical or high) are marked as requiring
  approval and never auto-execute (SECURITY.md section 14).
* All data quality markers follow API_CONTRACT.yaml provenance rules.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.intelligence.anomaly_detector import AnomalyResult, SensorAnomalyDetector
# Imported directly. The previous form was a three-level try/except cascade that
# fell back to `app.integrations.model_gateway.base` — a module removed when the
# gateway moved to `app/integrations/llm/` — and then to `ModelGatewayInterface
# = Any`, which would have silently disabled the type contract that keeps LLM
# access behind the gateway. A missing gateway interface must fail loudly at
# import, not degrade to `Any` (AI_DEVELOPMENT_RULES.md sections 4.4 and 26).
from app.integrations.llm.interface import (
    Message,
    ModelGatewayInterface,
    Role,
    TextGenerationRequest,
)

from app.telemetry.emitter import TelemetryEmitter
from app.telemetry.tracker import CostProvenance, CostTracker, CostType

logger = get_logger(__name__)

WORKFLOW_TYPE = "predictive_maintenance"

# Number of anomalous metrics that triggers LLM reasoning.
# Single-metric anomalies get a deterministic recommendation.
COMPLEX_ANOMALY_THRESHOLD = 2

# Anomaly score above which risk is elevated to high.
HIGH_RISK_ANOMALY_SCORE = 0.85

# Anomaly score above which risk is critical.
CRITICAL_RISK_ANOMALY_SCORE = 0.95


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Input validation result."""

    is_valid: bool
    errors: tuple[str, ...] = ()


class PredictiveMaintenanceWorkflow:
    """Three-step predictive maintenance workflow.

    Dependencies are injected, not created internally:
    * ``model_gateway`` — the only legal path to an LLM.
    * ``telemetry`` — every step emits events.
    * ``cost_tracker`` — LLM usage costs are recorded.
    """

    workflow_type: str = WORKFLOW_TYPE

    def __init__(
        self,
        *,
        model_gateway: ModelGatewayInterface,
        telemetry: TelemetryEmitter,
        cost_tracker: CostTracker,
        anomaly_detector: SensorAnomalyDetector | None = None,
        default_model: str = "predictive-maintenance-model",
    ) -> None:
        self._gateway = model_gateway
        self._telemetry = telemetry
        self._cost_tracker = cost_tracker
        self._detector = anomaly_detector or SensorAnomalyDetector()
        self._default_model = default_model

    # ---------------------------------------------------------------- public

    async def validate_input(self, input_data: dict[str, Any]) -> ValidationResult:
        """Validate that the input contains the minimum required fields."""
        errors: list[str] = []

        if input_data is None:
            return ValidationResult(is_valid=False, errors=("input_data is required",))

        if not input_data.get("machine_id"):
            errors.append("machine_id is required")

        sensor_readings = input_data.get("sensor_readings")
        if sensor_readings is None:
            errors.append("sensor_readings is required")
        elif not isinstance(sensor_readings, dict):
            errors.append("sensor_readings must be a dict")
        elif not sensor_readings:
            errors.append("sensor_readings must not be empty")

        if errors:
            return ValidationResult(is_valid=False, errors=tuple(errors))
        return ValidationResult(is_valid=True)

    async def execute(
        self, execution_id: str, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Run the full PdM pipeline.

        Returns the raw output dict to be formatted by ``format_output``.
        """
        machine_id = input_data["machine_id"]
        sensor_readings = input_data["sensor_readings"]
        maintenance_history = input_data.get("maintenance_history", [])
        machine_type = input_data.get("machine_type", "unknown")

        # ── Step A: Anomaly Detection (deterministic) ───────────────

        self._telemetry.emit(
            "workflow.step_completed",
            execution_id,
            {"step": "input_validation", "status": "passed"},
        )

        anomaly_result = self._detector.detect(sensor_readings)

        self._telemetry.emit(
            "workflow.step_completed",
            execution_id,
            {
                "step": "anomaly_detection",
                "is_anomalous": anomaly_result.is_anomalous,
                "anomaly_score": anomaly_result.anomaly_score,
                "anomalous_metrics": list(anomaly_result.anomalous_metrics),
            },
        )

        # Normal machine — return immediately, no LLM call.
        if not anomaly_result.is_anomalous:
            return self._build_normal_result(
                machine_id=machine_id,
                machine_type=machine_type,
                anomaly_result=anomaly_result,
            )

        # ── Step B: Root-Cause Reasoning ────────────────────────────

        is_complex = len(anomaly_result.anomalous_metrics) >= COMPLEX_ANOMALY_THRESHOLD
        llm_explanation: str | None = None
        model_used: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        total_tokens: int | None = None
        data_quality = "ACTUAL"  # Overridden to ESTIMATED if LLM is used.

        if is_complex:
            # Complex anomaly: invoke LLM for root-cause analysis.
            llm_result = await self._llm_root_cause(
                execution_id=execution_id,
                machine_id=machine_id,
                machine_type=machine_type,
                anomaly_result=anomaly_result,
                maintenance_history=maintenance_history,
            )
            llm_explanation = llm_result["explanation"]
            model_used = llm_result["model_used"]
            prompt_tokens = llm_result.get("prompt_tokens")
            completion_tokens = llm_result.get("completion_tokens")
            total_tokens = llm_result.get("total_tokens")
            data_quality = "ESTIMATED"  # LLM output is model inference.
        else:
            # Simple anomaly: deterministic recommendation, no LLM.
            llm_explanation = self._deterministic_explanation(anomaly_result)

        # ── Step C: Risk Classification ─────────────────────────────

        risk_level = self._classify_risk(anomaly_result)
        priority = risk_level  # Priority mirrors risk for maintenance.
        requires_approval = risk_level in ("critical", "high")

        recommendation_type = self._select_recommendation_type(anomaly_result)
        recommended_action = self._build_recommended_action(
            anomaly_result=anomaly_result,
            llm_explanation=llm_explanation,
            risk_level=risk_level,
        )

        self._telemetry.emit(
            "workflow.step_completed",
            execution_id,
            {
                "step": "risk_classification",
                "risk_level": risk_level,
                "requires_approval": requires_approval,
                "used_llm": is_complex,
            },
        )

        return {
            "machine_id": machine_id,
            "machine_type": machine_type,
            "recommendation_type": recommendation_type,
            "priority": priority,
            "description": llm_explanation or "",
            "estimated_cost_usd": None,  # No fabricated costs.
            "estimated_downtime_hours": None,
            "risk_level": risk_level,
            "confidence_score": round(1.0 - anomaly_result.anomaly_score * 0.3, 4),
            "recommended_action": recommended_action,
            "data_quality": data_quality,
            "anomaly_details": {
                "is_anomalous": anomaly_result.is_anomalous,
                "anomaly_score": anomaly_result.anomaly_score,
                "anomalous_metrics": list(anomaly_result.anomalous_metrics),
                "missing_metrics": list(anomaly_result.missing_metrics),
            },
            "requires_approval": requires_approval,
            "model_used": model_used,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    async def format_output(self, raw_output: dict[str, Any]) -> dict[str, Any]:
        """Format the raw output into the API response shape.

        Follows the ``MaintenanceRecommendation`` schema from API_CONTRACT.yaml.
        """
        return {
            "machine_id": raw_output["machine_id"],
            "recommendation_type": raw_output["recommendation_type"],
            "priority": raw_output["priority"],
            "description": raw_output["description"],
            "estimated_cost_usd": raw_output.get("estimated_cost_usd"),
            "estimated_downtime_hours": raw_output.get("estimated_downtime_hours"),
            "risk_level": raw_output["risk_level"],
            "confidence_score": raw_output["confidence_score"],
            "recommended_action": raw_output["recommended_action"],
            "data_quality": raw_output["data_quality"],
            "anomaly_details": raw_output["anomaly_details"],
            "requires_approval": raw_output.get("requires_approval", False),
        }

    # --------------------------------------------------------------- private

    def _build_normal_result(
        self,
        *,
        machine_id: str,
        machine_type: str,
        anomaly_result: AnomalyResult,
    ) -> dict[str, Any]:
        """Build a result for a machine with no anomalies detected."""
        return {
            "machine_id": machine_id,
            "machine_type": machine_type,
            "recommendation_type": "preventive",
            "priority": "low",
            "description": "All sensor readings are within normal operating ranges. "
            "Continue routine monitoring and scheduled maintenance.",
            "estimated_cost_usd": None,
            "estimated_downtime_hours": None,
            "risk_level": "low",
            "confidence_score": 1.0,
            "recommended_action": "Continue scheduled preventive maintenance.",
            "data_quality": "ACTUAL",
            "anomaly_details": {
                "is_anomalous": False,
                "anomaly_score": anomaly_result.anomaly_score,
                "anomalous_metrics": [],
                "missing_metrics": list(anomaly_result.missing_metrics),
            },
            "requires_approval": False,
            "model_used": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }

    async def _llm_root_cause(
        self,
        *,
        execution_id: str,
        machine_id: str,
        machine_type: str,
        anomaly_result: AnomalyResult,
        maintenance_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Invoke the LLM for complex root-cause reasoning.

        System and user content are strictly separated (SECURITY.md section 9).
        Maintenance history and sensor data are treated as untrusted user content.
        """
        self._telemetry.emit(
            "llm.call_started",
            execution_id,
            {"model": self._default_model, "reason": "complex_root_cause"},
        )

        # Build prompt with strict system/user separation.
        system_prompt = (
            "You are a manufacturing maintenance expert. Analyse the anomalous "
            "sensor readings and maintenance history provided. Return a JSON object "
            "with a single key 'explanation' containing a concise root-cause "
            "analysis and recommended corrective action. Do not include any "
            "information not derived from the provided data."
        )

        # Context filtering: limit history to last 10 events to respect token
        # budget (SECURITY.md section 10, context guardrails).
        filtered_history = maintenance_history[-10:] if maintenance_history else []

        # Anomaly details as structured data (not system instructions).
        metric_summary = {
            name: {
                "value": detail.value,
                "z_score": detail.z_score,
                "threshold": detail.z_threshold,
                "is_anomalous": detail.is_anomalous,
            }
            for name, detail in anomaly_result.metric_details.items()
        }

        user_content = json.dumps(
            {
                "machine_id": machine_id,
                "machine_type": machine_type,
                "anomalous_metrics": list(anomaly_result.anomalous_metrics),
                "anomaly_score": anomaly_result.anomaly_score,
                "sensor_analysis": metric_summary,
                "recent_maintenance_history": filtered_history,
            },
            default=str,
        )

        messages_tuple = (
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_content),
        )

        if hasattr(self._gateway, "generate_text"):
            req = TextGenerationRequest(
                model=self._default_model,
                messages=messages_tuple,
                response_format="json_object",
                temperature=0.3,
                max_output_tokens=512,
            )
            response = await self._gateway.generate_text(req)
        else:
            req = TextGenerationRequest(
                model=self._default_model,
                messages=list(messages_tuple),
                response_format="json_object",
                temperature=0.3,
                max_output_tokens=512,
            )
            response = await self._gateway.generate(req)

        # Record cost.
        usage = response.usage
        cost_usd = 0.0  # Actual cost comes from the provider.
        provenance = CostProvenance.ESTIMATED

        is_complete = getattr(
            usage,
            "is_complete",
            getattr(
                usage,
                "is_reported",
                usage.input_tokens is not None and usage.output_tokens is not None,
            ),
        )

        if is_complete and usage.input_tokens is not None:
            # Use provider-reported tokens for cost estimation.
            # No pricing is invented; we record the token counts.
            cost_usd = 0.0  # Pricing comes from model registry, not hardcoded.
            provenance = CostProvenance.ESTIMATED

        self._cost_tracker.record_cost(
            execution_id=execution_id,
            cost_type=CostType.LLM_INFERENCE,
            amount_usd=cost_usd,
            description=f"PdM root-cause analysis for machine {machine_id}",
            provenance=provenance,
        )

        self._telemetry.emit(
            "llm.call_completed",
            execution_id,
            {
                "model": response.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "latency_ms": response.latency_ms,
            },
        )

        self._telemetry.emit(
            "cost.recorded",
            execution_id,
            {
                "cost_type": CostType.LLM_INFERENCE,
                "amount_usd": cost_usd,
                "provenance": provenance,
            },
        )

        # Parse LLM response.  If parsing fails, use raw content.
        explanation = response.content
        try:
            parsed = json.loads(response.content)
            if isinstance(parsed, dict) and "explanation" in parsed:
                explanation = parsed["explanation"]
        except (json.JSONDecodeError, TypeError):
            pass  # Use raw content as the explanation.

        return {
            "explanation": explanation,
            "model_used": response.model,
            "prompt_tokens": usage.input_tokens,
            "completion_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }

    def _deterministic_explanation(self, anomaly_result: AnomalyResult) -> str:
        """Build a deterministic explanation for simple single-metric anomalies."""
        parts: list[str] = []
        for metric_name in anomaly_result.anomalous_metrics:
            detail = anomaly_result.metric_details.get(metric_name)
            if detail:
                direction = "above" if detail.value > detail.mean else "below"
                parts.append(
                    f"{metric_name} is {direction} normal range "
                    f"(value={detail.value}, expected≈{detail.mean}±{detail.std}, "
                    f"z-score={detail.z_score})"
                )
        if parts:
            return "Anomaly detected: " + "; ".join(parts) + ". Recommend inspection."
        return "Anomaly detected in sensor readings. Recommend inspection."

    def _classify_risk(self, anomaly_result: AnomalyResult) -> str:
        """Classify risk level based on anomaly severity.

        This is a deterministic rule, not an LLM decision
        (AI_DEVELOPMENT_RULES.md section 7).
        """
        score = anomaly_result.anomaly_score
        num_anomalous = len(anomaly_result.anomalous_metrics)

        if score >= CRITICAL_RISK_ANOMALY_SCORE or num_anomalous >= 4:
            return "critical"
        if score >= HIGH_RISK_ANOMALY_SCORE or num_anomalous >= 3:
            return "high"
        if num_anomalous >= 2:
            return "medium"
        return "medium"  # Any anomaly is at least medium risk.

    def _select_recommendation_type(self, anomaly_result: AnomalyResult) -> str:
        """Select the recommendation type based on anomaly characteristics."""
        if len(anomaly_result.anomalous_metrics) >= 3:
            return "corrective"
        if anomaly_result.anomaly_score >= HIGH_RISK_ANOMALY_SCORE:
            return "corrective"
        return "condition_based"

    def _build_recommended_action(
        self,
        *,
        anomaly_result: AnomalyResult,
        llm_explanation: str | None,
        risk_level: str,
    ) -> str:
        """Build the recommended action string."""
        if risk_level in ("critical", "high"):
            prefix = (
                "RECOMMENDATION ONLY — requires authorized approval before execution. "
            )
        else:
            prefix = ""

        if llm_explanation and len(anomaly_result.anomalous_metrics) >= COMPLEX_ANOMALY_THRESHOLD:
            return f"{prefix}Schedule urgent inspection. {llm_explanation}"

        metrics = ", ".join(anomaly_result.anomalous_metrics)
        return f"{prefix}Inspect anomalous sensor(s): {metrics}. Schedule condition-based maintenance."
