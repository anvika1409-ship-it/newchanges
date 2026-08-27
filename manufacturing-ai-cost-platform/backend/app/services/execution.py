"""AI Execution Service — orchestrates workflow execution.

Coordinates workflow lookup, input validation, execution, telemetry and cost
tracking.  This is the single entry point used by the API layer to run any
registered AI workflow (ARCHITECTURE.md section 6).

The service does not own the database transaction — it delegates persistence to
the caller or a repository when one is provided.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.core.logging import get_logger

# Imported directly. The removed fallback ended in `ModelGatewayInterface =
# Any`, which would have accepted any object as a gateway.
from app.integrations.llm.interface import ModelGatewayInterface
from app.telemetry.emitter import TelemetryEmitter
from app.telemetry.tracker import CostTracker
from app.workloads.predictive_maintenance import PredictiveMaintenanceWorkflow
from app.workloads.supply_chain import SupplyChainWorkflow

logger = get_logger(__name__)

# Registry of supported workflow types.
# New workflows are added here as they are implemented.
_WORKFLOW_TYPES: dict[str, type] = {
    "predictive_maintenance": PredictiveMaintenanceWorkflow,
    "supply_chain": SupplyChainWorkflow,
}


class AIExecutionService:
    """Orchestrator for AI workflow executions.

    Receives an execution request, looks up the workflow, validates input,
    runs execution, and returns the formatted result with telemetry and cost
    tracking.
    """

    def __init__(
        self,
        *,
        model_gateway: ModelGatewayInterface,
        telemetry: TelemetryEmitter,
        cost_tracker: CostTracker,
        default_model: str = "predictive-maintenance-model",
    ) -> None:
        self._gateway = model_gateway
        self._telemetry = telemetry
        self._cost_tracker = cost_tracker
        self._default_model = default_model

    async def execute(
        self,
        workflow_type: str,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an AI workflow.

        Args:
            workflow_type: Registered workflow type identifier.
            input_data: Workflow-specific input payload.

        Returns:
            Structured result dict containing execution_id, status, output,
            cost and timing information.

        Raises:
            ValueError: If the workflow type is not registered.
        """
        execution_id = str(uuid.uuid4())

        # Look up workflow class.
        workflow_class = _WORKFLOW_TYPES.get(workflow_type)
        if workflow_class is None:
            supported = ", ".join(sorted(_WORKFLOW_TYPES.keys()))
            raise ValueError(
                f"Unknown workflow type: {workflow_type}. "
                f"Supported types: {supported}"
            )

        # Instantiate with injected dependencies.
        workflow = workflow_class(
            model_gateway=self._gateway,
            telemetry=self._telemetry,
            cost_tracker=self._cost_tracker,
            default_model=self._default_model,
        )

        # Validate input.
        validation = await workflow.validate_input(input_data)
        if not validation.is_valid:
            self._telemetry.emit(
                "workflow.failed",
                execution_id,
                {
                    "workflow_type": workflow_type,
                    "reason": "validation_failed",
                    "errors": list(validation.errors),
                },
            )
            return {
                "execution_id": execution_id,
                "workflow_type": workflow_type,
                "status": "failed",
                "error": f"Validation failed: {', '.join(validation.errors)}",
            }

        # Execute.
        self._telemetry.emit(
            "workflow.started",
            execution_id,
            {"workflow_type": workflow_type},
        )

        start_time = time.monotonic()
        try:
            raw_output = await workflow.execute(execution_id, input_data)
            execution_time = time.monotonic() - start_time

            formatted_output = await workflow.format_output(raw_output)
            total_cost = self._cost_tracker.get_total_cost(execution_id)

            self._telemetry.emit(
                "workflow.completed",
                execution_id,
                {
                    "workflow_type": workflow_type,
                    "execution_time_seconds": round(execution_time, 4),
                    "cost_usd": total_cost,
                },
            )

            return {
                "execution_id": execution_id,
                "workflow_type": workflow_type,
                "status": "completed",
                "output": formatted_output,
                "cost_usd": total_cost,
                "execution_time_seconds": round(execution_time, 4),
                "model_used": raw_output.get("model_used"),
                "prompt_tokens": raw_output.get("prompt_tokens"),
                "completion_tokens": raw_output.get("completion_tokens"),
                "total_tokens": raw_output.get("total_tokens"),
            }

        except Exception as e:
            execution_time = time.monotonic() - start_time
            self._telemetry.emit(
                "workflow.failed",
                execution_id,
                {
                    "workflow_type": workflow_type,
                    "reason": "execution_error",
                    "error": str(e),
                    "execution_time_seconds": round(execution_time, 4),
                },
            )
            raise
